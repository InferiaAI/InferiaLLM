# Spec: inferia-worker — InferiaLLM integration (iteration 2)

- **Date:** 2026-05-13
- **Branch:** `feat/inferia-worker-extraction` (continues from iteration 1)
- **Iteration:** 2 — Backend wiring + admin APIs (no dashboard UI)

## 1. Goal

Make the `inferia-worker` integration usable from within InferiaLLM by:

- Wiring `WorkerAuth` / `WorkerRegistry` / `WorkerController` into the orchestration server at startup so the existing `api/workers.py` router goes live.
- Registering `WorkerDeploymentStrategy` in `model_deployment/worker_main.py` so worker-pool deployments flow through the WS control channel.
- Adding admin-facing APIs the dashboard (or any operator tool) will eventually call to: mint a bootstrap token, list connected workers in a pool, and revoke a worker.
- Generating + persisting a per-pool `inference_token` shared by all workers in the pool.

Dashboard UI is **out of scope** in this iteration — only the backend wiring + APIs are delivered.

## 2. Scope

### In scope

- New SQL migration adding `compute_pools.inference_token` (text, nullable).
- New repository methods on the inventory and pool repos.
- New `api/admin_workers.py` router exposing three endpoints under `/v1/admin/workers/...` protected by the existing user-JWT + RBAC middleware.
- Wiring: construct `WorkerAuth`, `WorkerRegistry`, `WorkerController` at startup; mount the worker router and the admin router; pass `WorkerController` into the deployment strategy registry.
- ≥ 95% unit-test coverage on the new modules.

### Out of scope (called out explicitly)

- Dashboard React/TypeScript changes (separate iteration).
- Multi-replica control-plane support — `WorkerRegistry` lives in-process; multi-replica needs a shared registry (Redis, etc.). MVP assumes single orchestration process.
- Full JWT revocation: the `DELETE` admin endpoint disconnects a worker and marks it terminated; the issued JWT remains valid until expiry. A revocation list is a follow-up.
- Inference-token rotation UI / endpoint. The repo method exists; surfacing it via an HTTP endpoint is deferred.
- Per-worker inference tokens. MVP uses a single per-pool secret.

## 3. Architecture

```
                       orchestration server (FastAPI)
┌─────────────────────────────────────────────────────────────────┐
│  api/workers.py        worker-facing: register + WS channel     │
│  api/admin_workers.py  NEW — user-JWT + RBAC for dashboard      │
│                                                                 │
│  worker_controller/                                             │
│    WorkerAuth      (from JWT_SECRET_KEY)                        │
│    WorkerRegistry  (in-memory; queried by list-workers)         │
│    WorkerController (used by WorkerDeploymentStrategy)          │
│                                                                 │
│  model_deployment/worker_main.py                                │
│    runtime_strategies["worker"] = WorkerDeploymentStrategy(...) │
│                                                                 │
│  repositories/                                                  │
│    inventory_repo: upsert_worker, mark_ready, update_heartbeat, │
│                    list_workers, mark_terminated                │
│    pool_repo:      get_or_generate_inference_token,             │
│                    rotate_inference_token                       │
│                                                                 │
│  compute_pools.inference_token  (NEW column)                    │
└─────────────────────────────────────────────────────────────────┘
```

The admin router is the **only new HTTP surface** in this iteration. The worker-facing router (`/v1/workers/register` + `/v1/workers/channel`) was added in iteration 1.

## 4. Components

### 4.1 Schema

`infra/schema/migrations/20260513b_add_pool_inference_token.sql`:

```sql
ALTER TABLE compute_pools
ADD COLUMN IF NOT EXISTS inference_token text;
```

Nullable. Generated lazily on first mint or pool create.

### 4.2 Inventory repository

New methods on the inventory repo (path: `services/orchestration/repositories/inventory_repo.py` or wherever the project keeps its existing inventory repo).

```python
async def upsert_worker(
    self,
    *,
    pool_id: str,
    node_name: str,
    advertise_url: str,
    allocatable: dict[str, str],
) -> dict:
    """Insert or update a (pool_id, node_name) worker row. Returns the row.
    Raises DuplicateNodeError if (pool_id, node_name) is held by a
    non-worker-kind row (cannot be reused)."""

async def mark_ready(self, *, node_id: str) -> None:
    """Transition state from 'provisioning' → 'ready'. No-op if already ready."""

async def update_heartbeat(
    self,
    *,
    node_id: str,
    used: dict[str, str],
    loaded_models: list[str],
) -> None:
    """Persist the latest heartbeat. Updates last_heartbeat, metadata.used,
    metadata.loaded_models."""

async def list_workers(self, *, pool_id: str) -> list[dict]:
    """Return all agent_kind='worker' rows for the pool, ordered by created_at."""

async def mark_terminated(self, *, node_id: str) -> None:
    """Transition the row to state='terminated'. Idempotent."""
```

`DuplicateNodeError` is the existing exception type from `api/workers.py`; import lives in the inventory repo module after this change.

### 4.3 Pool repository

```python
async def get_or_generate_inference_token(self, *, pool_id: str) -> str:
    """Return the pool's inference_token; generate a 32-byte URL-safe random
    token (using secrets.token_urlsafe(32)) and persist it on first call.
    Concurrent first-callers see the same token thanks to INSERT/UPDATE
    serialisation."""

async def rotate_inference_token(self, *, pool_id: str) -> str:
    """Regenerate the inference_token; persist + return the new value.
    Existing workers using the old token will fail inference auth after
    rotation — operator is expected to redeploy them."""
```

### 4.4 Admin router

`services/orchestration/api/admin_workers.py`. All endpoints depend on the existing user-auth middleware + RBAC; no special middleware is added.

```python
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, validator

router = APIRouter(prefix="/v1/admin/workers")


# POST /v1/admin/workers/tokens — mint a bootstrap token + env snippet
class MintRequest(BaseModel):
    pool_id: str
    ttl_hours: int = Field(default=1, ge=1, le=24)


class MintResponse(BaseModel):
    bootstrap_token: str
    expires_at: int  # unix seconds
    pool_id: str
    control_plane_url: str
    inference_token: str
    env_snippet: str


# GET /v1/admin/workers/pool/{pool_id} — list workers in a pool
class WorkerView(BaseModel):
    node_id: str
    node_name: str
    advertise_url: str
    agent_kind: str
    state: str
    connected: bool
    last_heartbeat: str | None
    used: dict[str, str]
    loaded_models: list[str]
    allocatable: dict[str, str]


class ListResponse(BaseModel):
    workers: list[WorkerView]


# DELETE /v1/admin/workers/{node_id} — disconnect + mark terminated; 204
```

The endpoints use `Depends(require_permission(...))` for RBAC. The exact RBAC dependency name to match: whatever `api_gateway/rbac` already exposes (e.g. `require_permission("deployment:create")`).

`env_snippet` is built as:

```text
CONTROL_PLANE_URL={settings.control_plane_external_url}
BOOTSTRAP_TOKEN={bootstrap_token}
POOL_ID={pool_id}
NODE_NAME=  # operator fills in
WORKER_ADVERTISE_URL=  # operator fills in
INFERENCE_TOKEN={inference_token}
```

`settings.control_plane_external_url` is a new env var (`CONTROL_PLANE_EXTERNAL_URL`) injected on the orchestration service so the snippet contains an externally-reachable URL even when the service binds internally.

### 4.5 Server wiring

`services/orchestration/server.py` (where `app = FastAPI(...)` is constructed) gains:

```python
worker_auth = WorkerAuth(
    secret_key=settings.jwt_secret_key,
    algorithm=settings.jwt_algorithm,
)
worker_registry = WorkerRegistry()
worker_controller = WorkerController(worker_registry)

api_workers.configure(worker_auth, worker_registry, inventory_repo)
api_admin_workers.configure(
    worker_auth=worker_auth,
    worker_registry=worker_registry,
    inventory_repo=inventory_repo,
    pool_repo=pool_repo,
    control_plane_external_url=settings.control_plane_external_url,
)

app.include_router(workers_router)
app.include_router(admin_workers_router)
```

And `model_deployment/worker_main.py`:

```python
runtime_strategies = {
    "vllm": vllm_strategy,
    "localai": localai_strategy,
    "worker": WorkerDeploymentStrategy(
        placement_engine=placement_engine,
        scheduler_engine=scheduler_engine,
        worker_controller=worker_controller,
    ),
}
```

## 5. Protocol

No protocol changes. Worker ↔ control-plane wire format is unchanged from iteration 1.

The new admin endpoints follow standard FastAPI/JSON conventions; user-JWT auth is identical to other orchestration admin endpoints (`Authorization: Bearer <user_jwt>`).

## 6. Failure handling

| Failure | Behaviour |
|---|---|
| Pool not found on mint | 404 `pool not found` |
| Pool terminated/terminating | 409 `pool is terminating, cannot add workers` |
| User missing RBAC permission | 403 (existing middleware) |
| Invalid `pool_id` (not a UUID) | 422 (Pydantic) |
| `ttl_hours` out of range [1, 24] | 422 (Pydantic) |
| Revoke a non-existent node | 404 |
| WS close on revoke fails | Log warning; still return 204 (DB state was authoritative) |
| `inference_token` generation contention | Last writer wins; both callers eventually read the same token from the persisted row |
| `WorkerRegistry` membership stale (process restarted but DB unaware) | List endpoint reports `connected: false`; workers reconnect within their backoff window |

## 7. Security

- **Admin endpoints**: protected by the same user-JWT + RBAC system the rest of the orchestration API uses. No new auth surface.
- **`inference_token`**: 32-byte URL-safe random (`secrets.token_urlsafe(32)`), stored as plain text in `compute_pools.inference_token`. Justification: it's a per-pool shared secret with no human handling beyond the dashboard mint flow, and the column is in the same DB as `JWT_SECRET_KEY`-decrypted data; encrypting at the column level would not raise the security bar meaningfully.
- **Bootstrap-token scope**: scope=`worker:bootstrap`, default TTL 1h, capped at 24h. Tokens are not single-use — accepted risk for MVP.
- **Revocation**: `DELETE` disconnects the live WS but the worker JWT remains valid until expiry. A full JWT revocation list is a follow-up.

## 8. Configuration

New env var on the orchestration service:

| Var | Required | Default | Notes |
|---|---|---|---|
| `CONTROL_PLANE_EXTERNAL_URL` | yes | — | URL pasted into worker env snippets (e.g. `https://control.example.com`). Distinct from the internal bind URL. |

Existing `JWT_SECRET_KEY` is reused for worker-JWT minting (matches iteration 1 design).

## 9. Testing

| Suite | Coverage |
|---|---|
| Migration | Applies cleanly, column NULLable, no backfill needed. |
| `inventory_repo.upsert_worker` | New row on first call; same `node_id` on repeat; conflict with non-worker row → `DuplicateNodeError`; respects unique partial index. |
| `inventory_repo.list_workers` | Returns only `agent_kind='worker'`; orders by `created_at`; empty pool returns `[]`. |
| `inventory_repo.update_heartbeat` | Persists `used` + `loaded_models`; updates `last_heartbeat`. |
| `inventory_repo.mark_ready` / `mark_terminated` | State transitions; idempotent. |
| `pool_repo.get_or_generate_inference_token` | First call generates; second returns same; concurrent first-calls converge. |
| `pool_repo.rotate_inference_token` | Generates new value; persists. |
| `api/admin_workers.py` | Mint happy path returns all 6 fields incl. env_snippet; ttl_hours validated; 401 without auth; 403 without perm; 404 pool not found; 409 pool terminated. List returns merged DB+registry view; connected=true only when the node is in the registry. Revoke marks state and closes WS; double-revoke idempotent. |
| `worker_main.py` strategy registration | `runtime_strategies["worker"]` is a `WorkerDeploymentStrategy`. |
| Server startup smoke | App factory mounts both routers; `api.workers.configure(...)` called. |

Coverage gate: ≥ 95% on `services/orchestration/api/admin_workers.py`, `services/orchestration/repositories/inventory_repo.py` (new methods only), `services/orchestration/repositories/pool_repo.py` (new methods only).

## 10. Migration & rollout

1. Land this iteration on the same `feat/inferia-worker-extraction` branch. Worker-facing endpoints already exist but are not yet mounted; this iteration mounts them.
2. After this branch merges, an operator can:
   - Use the admin API (via curl or any HTTP client) to mint a bootstrap token for an existing pool.
   - Paste the returned `env_snippet` into the inferia-worker repo's `.env` and run `docker compose up`.
   - The worker registers, connects, heartbeats, and is visible via the list endpoint.
   - A deployment of `agent_kind='worker'` against the pool routes through `WorkerDeploymentStrategy` and the worker container is launched on the GPU host.
3. The dashboard work in iteration 3 will surface mint/list/revoke as buttons + tables; no API changes expected.

## 11. Open questions resolved

- **Iteration scope:** API + wiring only, no UI.
- **Admin endpoint auth:** existing user-JWT + RBAC.
- **Mint response shape:** token + env_snippet block.
- **`INFERENCE_TOKEN` source:** per-pool secret stored in `compute_pools.inference_token`.
- **Connection state in list endpoint:** computed from `WorkerRegistry.list_nodes()` at request time, merged with DB rows.

## 12. Non-goals / explicit YAGNI

- Multi-replica orchestration with shared registry.
- Encrypted `inference_token` column.
- JWT revocation list.
- Per-worker inference tokens.
- Dashboard UI.
- Inference-token rotation HTTP endpoint.

# Spec: Node-centric refactor (drop public pool concept)

- **Date:** 2026-05-14
- **Branch:** `feat/node-centric` (cut from `feat/inferia-worker-extraction`)
- **Iteration:** all-in-one (Iter A + B + C combined per operator request)

## 1. Goal

The product is still in development, and the pool-first model creates friction: operators must create a pool before they can do anything, even when they only want a single GPU. This iteration removes the pool concept from the public surface entirely. The first-class entity in the UI, API, and CLI becomes the **Node** (a row in `compute_inventory`). Every node carries a set of free-form labels. Existing provider logic (Nosana, Akash, k8s, inferia-worker) keeps working — its operations are just invoked at the *add-node* layer instead of being wrapped in a pool first.

Deployments accept either a pinned `node_id` or a `selector` (a label map). The scheduler picks a matching ready node.

## 2. Scope

### In scope

- New SQL migration: `compute_inventory.labels jsonb` + GIN index + a `__default__` pool per org for FK invariance.
- New backend router `api/nodes.py` and adapter-level `provision_single_node` for Nosana, Akash, worker.
- Deployment payload + placement_engine accept `node_id` or `selector` and stop accepting `pool_id`.
- UI: sidebar entry "Compute Pools" → "Compute Nodes". New list page, node detail page with labels editor, Add Node wizard with three cards (Nosana, Akash, Self-hosted).
- CLI: `inferiallm node {add,list,labels,rm}` mirroring the web flow.
- Pool-related public API surface is removed from the api_gateway proxy and the dashboard. Pool tables stay in the DB for FK only; no operator-facing CRUD.
- ≥ 95% test coverage on new modules.

### Out of scope

- `inferia-worker` repo changes (already node-centric — only gets a new dashboard entry point).
- Provider auth / credential storage UX (unchanged).
- Inference routing internals (still consume `deployment.endpoint`).
- A label-administration page (label keys are free-form; whitelists are a follow-up).
- Optimistic-concurrency on label edits (last-write-wins for Iter 1; documented in §6 as a known limitation).

## 3. Architecture

```
                Operator                                       (web UI / CLI)
                   │
                   │  /v1/nodes/*                                     /v1/admin/workers/*  (worker mgmt)
                   ▼
        ┌─────────────────────────────────────────────────────────────┐
        │              api_gateway (user-JWT + RBAC)                  │
        └──────┬──────────────────────────────────────────────────────┘
               │ /api/v1/nodes/*
               ▼
        ┌─────────────────────────────────────────────────────────────┐
        │           orchestration: api/nodes.py (NEW)                 │
        │  ┌──────────────────────────────────────────────────────┐   │
        │  │ list_nodes(labels=?)                                  │   │
        │  │ get_node(id)                                          │   │
        │  │ patch_labels(id, labels)                              │   │
        │  │ delete_node(id)                                       │   │
        │  │ add_node(provider, spec) ──► provision_single_node()  │   │
        │  └──────────────────────────────────────────────────────┘   │
        │                                                              │
        │  inventory_repo.list_nodes_by_labels(...)                   │
        │  inventory_repo.set_labels(node_id, labels)                 │
        │                                                              │
        │  adapter_engine ──► provision_single_node()                 │
        │    nosana → submit one Nosana job, persist node row         │
        │    akash  → one Akash deployment, persist node row          │
        │    worker → already a node (created on /workers/register)   │
        │    k8s    → keep current shape, hidden in UI                │
        │                                                              │
        │  deployment.deploy:                                          │
        │    payload accepts { node_id }  OR  { selector: {...} }     │
        │    placement_engine reads labels to pick a node             │
        │                                                              │
        │  compute_pools (still in DB; ONE default-pool-per-org row)  │
        │  compute_inventory (UI's primary entity; +labels jsonb)     │
        └─────────────────────────────────────────────────────────────┘
```

Conceptual flip: from pool-first to node-first. Pools remain in the schema for FK invariance and exactly one `__default__` row per org; the operator never names one.

## 4. Components

### 4.1 Schema

`infra/schema/migrations/20260515_node_centric.sql`:

```sql
ALTER TABLE compute_inventory
ADD COLUMN IF NOT EXISTS labels jsonb NOT NULL DEFAULT '{}'::jsonb;

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_compute_inventory_labels
ON compute_inventory USING GIN (labels);

-- Backfill: ensure every existing org has a __default__ pool so node-add
-- can rely on its presence.
INSERT INTO compute_pools (
  id, pool_name, owner_type, owner_id, provider, pool_type,
  allowed_gpu_types, max_cost_per_hour, scheduling_policy,
  provider_pool_id, is_active
)
SELECT
  gen_random_uuid(), '__default__', 'organization', o.id, 'on_prem',
  'job', ARRAY['any']::text[], 0, '{}'::jsonb,
  'default:' || o.id::text, true
FROM organizations o
WHERE NOT EXISTS (
  SELECT 1 FROM compute_pools p
  WHERE p.owner_id = o.id::text AND p.pool_name = '__default__'
);
```

### 4.2 Backend — orchestration

| File | Disposition | Surface |
|---|---|---|
| `services/orchestration/api/nodes.py` | NEW | Endpoints: `GET /v1/nodes`, `GET /v1/nodes/{id}`, `PATCH /v1/nodes/{id}/labels`, `DELETE /v1/nodes/{id}`, `POST /v1/nodes/add/{provider}` where `provider ∈ {worker, nosana, akash}`. |
| `services/orchestration/repositories/inventory_repo.py` | extend | `list_nodes(org_id, selector=None)`, `get_node(id)`, `set_labels(id, labels)`, `soft_delete_node(id)`, `create_provider_node(...)`. |
| `services/orchestration/repositories/pool_repo.py` | extend | `ensure_default_pool(org_id) -> uuid` (idempotent). |
| `services/orchestration/services/adapter_engine/base.py` | extend | New abstract `async def provision_single_node(self, *, pool_id, org_id, spec) -> dict` raising `NotImplementedError` by default. |
| `services/orchestration/services/adapter_engine/adapters/nosana/nosana_adapter.py` | implement | `provision_single_node` submits one Nosana job and persists one inventory row. |
| `services/orchestration/services/adapter_engine/adapters/akash/akash_adapter.py` | implement | same shape. |
| `services/orchestration/services/adapter_engine/adapters/worker/worker_adapter.py` | keep | NotImplementedError (workers self-provision via `api/workers.py`). |
| `services/orchestration/services/adapter_engine/adapters/k8s/k8s_adapter.py` | keep | unchanged; hidden from UI. |
| `services/orchestration/services/model_deployment/*` | refactor | `Deployment` payload schema now accepts `node_id` xor `selector`. Public API rejects `pool_id`. |
| `services/orchestration/services/placement_engine/*` | extend | `place_by_selector(selector, gpu_required, ...)` and `bind_to(node_id, gpu_required)`. The current pool-id path is removed from the public dispatcher. |

### 4.3 Backend — api_gateway

| Change | Detail |
|---|---|
| Add proxy | `/api/v1/nodes/{path:path}` → `v1/nodes/{path}` with method-aware RBAC (`GET` → `DEPLOYMENT_LIST`, `POST/PATCH` → `DEPLOYMENT_CREATE/UPDATE`, `DELETE` → `DEPLOYMENT_DELETE`). |
| Remove proxies | `/api/v1/pools/{path:path}`, `/api/v1/deployment/createpool`, `/api/v1/deployment/listPools`, `/api/v1/deployment/deletepool`, `/api/v1/deployment/stoppool`. |
| Keep | `/api/v1/admin/workers/*`, `/api/v1/deployments/*`, `/api/v1/inventory/*`. |

### 4.4 CLI

| File | Disposition |
|---|---|
| `package/src/inferia/cli.py` | Register the `node` subcommand. |
| `package/src/inferia/cli_node.py` | NEW. Subcommands `node add {nosana,akash,worker} ...`, `node list [--label k=v ...]`, `node labels {set,get,del} <id> ...`, `node rm <id>`. |
| `package/src/inferia/cli_worker.py` | Keep — operator shortcut into the worker-add path. |

### 4.5 Dashboard

| File | Disposition |
|---|---|
| `apps/dashboard/src/App.tsx` | Route `/dashboard/compute/pools*` → permanent redirect to `/dashboard/compute/nodes*`. New routes: `/dashboard/compute/nodes`, `/dashboard/compute/nodes/new`, `/dashboard/compute/nodes/:id`. |
| Sidebar | "Compute Pools" → "Compute Nodes". |
| `apps/dashboard/src/pages/Compute/Instances.tsx` | Rewrite as the Nodes list. Columns: name, provider, state, GPU class, labels (chip row, truncated), last heartbeat. |
| `apps/dashboard/src/pages/Compute/InstanceDetail.tsx` | Repurpose as Node detail. Overview tab shows node telemetry; new Labels tab with the chip editor; existing Workers tab kept for worker-kind nodes. |
| `apps/dashboard/src/pages/Compute/NewPool.tsx` → `NewNode.tsx` | Rewrite. 3-card picker (Nosana / Akash / Self-hosted) → provider-specific add-node forms posting to `/v1/nodes/add/{provider}`. |
| `apps/dashboard/src/pages/NewDeployment.tsx` | Pool picker → tabbed input: "Pin to a node" (id picker) vs "Match by labels" (selector input). |
| `apps/dashboard/src/services/nodeService.ts` | NEW. Typed client for the new endpoints. |
| `apps/dashboard/src/services/workerService.ts` | Keep; the Add Worker modal becomes a sub-flow of NewNode (Self-hosted card). |
| `apps/dashboard/src/components/nodes/LabelEditor.tsx` | NEW. Tag-chip editor: add, remove, dedupe, key/value validation. |

## 5. Protocol — REST surface

### `POST /v1/nodes/add/worker`

```json
Request:
{
  "node_name": "string (1..255)",
  "advertise_url": "https://... (optional; operator fills in compose)",
  "labels": { "k": "v", ... }
}

Response 200:
{
  "node_id": "uuid",
  "bootstrap_token": "...",
  "expires_at": 1778800000,
  "control_plane_url": "https://...",
  "inference_token": "...",
  "env_snippet": "..."
}
```

### `POST /v1/nodes/add/nosana`

```json
Request:
{
  "gpu_type": "RTX 4090",
  "market_address": "...",
  "credential_name": "default",
  "labels": { "k": "v", ... }
}

Response 200:
{ "node_id": "uuid", "provider_instance_id": "...", "state": "provisioning" }
```

### `POST /v1/nodes/add/akash`

Same shape as Nosana; SDL fields replace market_address.

### `GET /v1/nodes`

Query string `labels=k1=v1,k2=v2` (AND, repeated comma-separated).

```json
Response 200:
{
  "nodes": [
    {
      "id": "uuid",
      "name": "string",
      "provider": "nosana|akash|worker|k8s",
      "agent_kind": "worker|nosana|akash|...",
      "state": "provisioning|ready|draining|terminated",
      "connected": true,
      "gpu_total": 1, "gpu_allocated": 0,
      "labels": { "k": "v" },
      "advertise_url": "...",
      "expose_url": "...",
      "last_heartbeat": "2026-05-14T...",
      "used": { "cpu_pct": "10.2", "mem_used": "12345" }
    }
  ]
}
```

### `GET /v1/nodes/{id}` — same row shape.

### `PATCH /v1/nodes/{id}/labels`

```json
Request:
{
  "add":    { "env": "prod" },         // upsert these keys
  "remove": [ "staging", "old_tag" ]   // unset these keys
}

Response 200: full node row.
```

### `DELETE /v1/nodes/{id}` → 204.

### Deployments

`POST /v1/deployments` body changes:

```json
// pinned
{ "model_id": "...", "node_id": "uuid", "gpu_required": 1, ... }

// selector
{ "model_id": "...", "selector": {"gpu":"h100","zone":"eu"}, "gpu_required": 1, ... }
```

Submitting both or neither → 422.

## 6. Failure handling

| Failure | Behaviour |
|---|---|
| `add/worker` node-name collides in default pool | 409 `node name already in use`. |
| `add/worker`: JWT signing key missing | 500 `control plane not ready`. |
| `add/nosana`: SDK error before submit | 502; no DB write. Form keeps inputs for retry. |
| `add/nosana`: submit ok + DB insert fails | 500; orphan-job reaper cleans up after 5 min; idempotent retry via `provider_instance_id` unique index. |
| Missing RBAC permission | 403 (existing middleware). |
| Validation: label key/value length, count > 32, control chars | 422 with field-level error. |
| Missing org_id on user JWT | 401 `re-login required`. |
| Selector query string malformed | 422 `expected key=value pairs`. |
| Node not found | 404. |
| Label edit on terminated node | 409 `node is terminated`. |
| `PATCH labels` add+remove same key | 422 `conflicting label op`. |
| Worker WS close failure during delete | Warning logged; DB transitions to `terminated`; 204 returned. |
| Nosana/Akash deprovision SDK error | DB state `terminating`; reaper retries every 30s; UI shows the in-progress chip. |
| Active deployments on the node being deleted | 409 `<n> active deployments on this node — stop them first`. |
| Selector matches zero nodes (deployment-create) | 422 `no nodes match selector`. |
| Selector matches but all are at capacity | 503 with `Retry-After: 30`. |
| Both node_id and selector supplied | 422 `provide exactly one`. |
| Node_id pointed at terminated row | 409 `node terminated`. |
| Migration: ALTER COLUMN already applied | No-op via `IF NOT EXISTS`. |
| Migration: GIN index half-built on crash | Re-running uses `CONCURRENTLY IF NOT EXISTS`. |
| Default-pool backfill rerun | Idempotent (`WHERE NOT EXISTS`). |
| UI: provider list API down | Three static cards from fallback (existing pattern). |
| UI: node list fetch fails | Empty state + Retry; toast with error detail. |
| Label-edit race (two operators editing same node) | **Known limitation, last-write-wins.** Documented; optimistic concurrency is a follow-up. |
| Deployment form, selector matches zero | Inline message + Submit disabled. |

## 7. Security

- The orchestration `InternalAuthMiddleware` continues to be the trust boundary for the api_gateway. All new `/v1/nodes/*` endpoints are under `InternalAuthMiddleware`; RBAC is enforced upstream in the api_gateway.
- Label keys/values are stored as plain JSON. They are not parsed as code. Validation rejects control chars, NUL bytes, and keys/values over the K8s-compatible length bounds.
- The default pool's UUID is a private bookkeeping detail; it never appears in any API response.
- Existing worker auth (bootstrap-JWT → worker-JWT) is unchanged.

## 8. Configuration

No new env vars in orchestration or api_gateway. Existing config (`JWT_SECRET_KEY`, `INTERNAL_API_KEY`, `CONTROL_PLANE_EXTERNAL_URL`) covers the new endpoints.

## 9. Testing

Coverage gate: ≥ 95% on every new module and every modified file.

| Suite | Cases |
|---|---|
| Migration | Applies fresh; idempotent on re-run; GIN index queryable; backfill creates one `__default__` row per org. |
| `inventory_repo.list_nodes` | Empty org → `[]`; single-label selector hits matching rows; multi-label selector is AND; terminated rows excluded by default; selectors with `.`/`-` work. |
| `inventory_repo.set_labels` | Idempotent for equal payload; add merges; remove unsets; 32-label cap; key length boundaries (0, 64, 253). |
| `inventory_repo.soft_delete_node` | Sets `terminated`; idempotent. |
| `pool_repo.ensure_default_pool` | First call creates + returns id; second call returns same id; concurrent first-callers converge. |
| Adapter `provision_single_node` (Nosana, Akash) | Returns valid node dict; SDK pre-insert error → no row; SDK ok + DB error → orphan warning; node_name collision → DuplicateNodeError. |
| `api/nodes.py` | Happy paths for GET/POST/PATCH/DELETE; 401, 403, 404, 409, 422 as listed in §6. |
| `model_deployment` selector path | Selector matches one → bound; matches many → highest-free-GPU wins; matches zero → 422; both fields → 422; terminated node_id → 409. |
| `placement_engine.place_by_selector` | Filters by labels then capacity; ties broken by `last_heartbeat` freshness; only `ready`/`draining`. |
| `cli_node.py` | `node add worker --name x --labels gpu=h100,zone=eu`; `node list --label gpu=h100`; `node labels set <id> env=prod`; `node rm <id>` returns 204; bad flags surface usage. |
| UI `nodeService.ts` | Client matches contract; selector serialised; label PATCH body shape. |
| UI `LabelEditor.tsx` (RTL) | Add chip, remove, dedupe; 63-char key cap; 32-label cap with disabled "+"; Enter to commit; keyboard nav. |
| UI `NewNode.tsx` | Each card routes to the right sub-form; submit hits the right endpoint; redirect to `/dashboard/compute/nodes/<id>` (or `?tab=workers` for self-hosted). |
| UI `NewDeployment.tsx` | Toggle between pin / selector; payload shape correct. |
| E2E smoke (live stack) | (a) Worker node from dashboard → `docker compose up` → ready+connected in <30s. (b) Nosana node → row appears in `provisioning`. (c) Deploy with label selector → endpoint reachable. |

### Files we delete (with tests)

- `apps/dashboard/src/pages/Compute/NewPool.tsx` and any related tests.
- `inventory_repo.get_pool_by_id`, `list_pools_*`, `set_pool_active`, `set_pool_lifecycle_state` — and their tests.
- api_gateway `/pools/`, `/deployment/createpool`, `/deployment/listPools`, `/deployment/deletepool`, `/deployment/stoppool` proxy entries.
- `services/orchestration/services/compute_pool_engine` modules left unused after the rip (concrete file list in the implementation plan).

### What we do NOT touch

- `inferia-worker` repo.
- `services/inference/*`.
- Provider credential storage / Settings → Providers UX.

## 10. Migration & rollout

1. Land on branch `feat/node-centric` from `feat/inferia-worker-extraction`.
2. Apply the SQL migration before the new orchestration build boots — order matters: the orchestration startup probes `compute_inventory.labels` lazily, so the column must exist.
3. Public API of the api_gateway changes in a breaking way (pool routes removed). Document this in the release notes: any external integration calling `/api/v1/deployment/createpool` etc. must move to `/api/v1/nodes/add/*`.
4. CLI and dashboard land in the same release so operators have a path forward at the moment of upgrade.

## 11. Non-goals / explicit YAGNI

- Optimistic concurrency on label edits (Iter 1 is last-write-wins).
- Org-level "recognised labels" whitelist UI.
- Pool restoration UX (pools are an internal detail and stay that way).
- k8s adapter UI surfacing (still works for code-level callers; deferred).
- Multi-replica orchestration with shared registries (still single-replica MVP).
- Deployment migration tool for existing deployments still carrying a `pool_id` payload — those deployments are out of scope; the spec assumes the product is in dev and there's nothing live to migrate.

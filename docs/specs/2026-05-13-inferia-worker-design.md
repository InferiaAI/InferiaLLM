# Spec: `inferia-worker` — Direct-managed GPU node agent

- **Date:** 2026-05-13
- **Branch:** `feat/inferia-worker-extraction` (InferiaLLM)
- **New repo:** `inferia-worker` (sibling to InferiaLLM)
- **Iteration:** 1 — Minimum viable cut-over

## 1. Goal

Move GPU-node-side code out of InferiaLLM (Python) into a new standalone Go service, `inferia-worker`, that runs on each direct-managed GPU host. InferiaLLM remains the control plane (web UI, auth, RBAC, scheduling, inference routing) and gains the ability to manage many GPU nodes by holding a long-lived connection to each worker.

A fresh GPU host (bare-metal, self-hosted server, or a cloud VM the operator provisions — EC2 G/P, GCP A2, Lambda, RunPod-VM, etc.) becomes part of the fleet by running `docker compose up`. Nothing else.

## 2. Scope

### In scope (iteration 1)

- New Go + Fiber service `inferia-worker` with: registration, heartbeat, control channel (WebSocket), pluggable runtime launcher (vLLM, Ollama, vllm-omni, infinity, triton, inferia-diffusion), local inference HTTP proxy.
- Control plane additions: `worker_controller` service, `/v1/workers/register` and `WS /v1/workers/channel` endpoints, bootstrap-token → worker-JWT exchange, routing inference to per-worker advertised URLs.
- Schema migration: new `compute_nodes.kind` column.
- Deletion of `services/orchestration/services/node-agent/`, `services/orchestration/services/llmd_runtime/`, `services/orchestration/services/llmd/spec_builder.py` (after extracting shared validators).
- `docker-compose.yml` in the worker repo such that `docker compose up` is sufficient to register and connect to InferiaLLM.
- ≥95% unit-test coverage on new/modified packages, plus integration tests for the docker-launcher path and the protocol contract.

### Out of scope (iteration 1, explicit)

- DePIN providers (Akash, Nosana) — completely untouched. Existing `services/orchestration/services/adapter_engine/adapters/{akash,nosana}/...` and the TypeScript `depin-sidecar` continue to work as today.
- mTLS, multi-GPU tensor-parallel per replica, model artifact cache management, autoscaler signals from worker, graceful drain on worker shutdown, WS reverse-tunnel for inference.

## 3. Architecture

```
┌─────────────────────────────────────┐         ┌──────────────────────────────────┐
│         InferiaLLM (Python)         │  WS     │      inferia-worker (Go/Fiber)   │
│  - Web UI / API gateway             │◄───────►│  - Registers + heartbeats        │
│  - Auth, RBAC, policies             │ control │  - Receives load/unload cmds     │
│  - Inference gateway (routes only)  │         │  - Spawns vLLM/Ollama containers │
│  - Model registry / scheduler /     │         │    via /var/run/docker.sock      │
│    placement / autoscaler           │  HTTP   │  - Exposes inference :8080       │
│  - NEW: worker_controller service   │────────►│    (OpenAI-compatible)           │
└─────────────────────────────────────┘ inference└──────────────────────────────────┘
        Control plane                                      Each GPU host
```

Two channels:
- **Control channel** — worker → CP, persistent WebSocket, JSON frames. Carries `Register`, `Heartbeat`, `LoadModel`, `UnloadModel`, `CommandResult`, `Hello`, `Ping`.
- **Inference channel** — CP → worker, direct HTTPS. Worker advertises its reachable URL at register time. Native SSE for streaming completions. Auth: shared `INFERENCE_TOKEN` header.

## 4. Components

### 4.1 `inferia-worker` (new Go repo)

| Package | Responsibility |
|---|---|
| `cmd/worker` | Process entry point. Parses env, wires deps, starts Fiber server + control channel. |
| `internal/config` | Env-driven config: `CONTROL_PLANE_URL`, `BOOTSTRAP_TOKEN`, `NODE_NAME`, `WORKER_ADVERTISE_URL`, `WORKER_LISTEN_ADDR`, `POOL_ID`, `INFERENCE_TOKEN`, log level. |
| `internal/control` | WebSocket client. Bootstrap → token exchange → reconnect-with-backoff → command dispatch loop. Heartbeat ticker. Command idempotency cache (5-min window). |
| `internal/telemetry` | CPU (`/proc/stat`), memory (`/proc/meminfo`), GPU (`nvidia-smi --query-gpu=… --format=csv,noheader,nounits`). |
| `internal/runtime` | `Launcher` interface and `dockerLauncher` implementation. Lifecycle: pull → run → readiness probe → manage. State machine: absent → pulling → starting → running → stopping → absent (or → failed). |
| `internal/runtime/recipes` | Registry of launch recipes ported from `services/orchestration/services/adapter_engine/adapters/nosana/job_builder.py`: `vllm`, `ollama`, `vllm-omni`, `infinity`, `triton`, `inferia-diffusion`. Each recipe = `{image, env, cmd, container_port, ready_probe_path}`. |
| `internal/runtime/dockerclient` | Thin wrapper over `github.com/docker/docker/client`: Pull, Run, Stop, Rm, Status, Events. |
| `internal/inference` | Fiber handlers proxying `/v1/*` to the currently-loaded model container's local port. Stream-aware. |
| `internal/auth` | Token store (in-memory + persisted to `/var/lib/inferia-worker/token`). Inference-token middleware (constant-time compare). |
| `internal/healthz` | `/healthz`, `/readyz`. |

#### Boundaries

- Worker never reads/writes the InferiaLLM database.
- Worker never touches DePIN provider SDKs.
- Worker only knows: what model to load + recipe args, where to heartbeat. No business logic, no end-user auth.

### 4.2 InferiaLLM additions

| Path | Responsibility |
|---|---|
| `services/orchestration/services/worker_controller/__init__.py` | Public surface used by scheduler / model_deployment. |
| `services/orchestration/services/worker_controller/registry.py` | In-memory `node_id → WebSocket` registry. Thread-safe. Replaces single-connection assumption. |
| `services/orchestration/services/worker_controller/protocol.py` | Pydantic models for every message type. Validation. |
| `services/orchestration/services/worker_controller/controller.py` | High-level: `load_model(node_id, spec)`, `unload_model(node_id, deployment_id)`. Sends + awaits `CommandResult` with timeout. Reconcile on reconnect. |
| `services/orchestration/services/worker_controller/auth.py` | Bootstrap-token validation. Worker-JWT mint (signed with existing `JWT_SECRET_KEY`, claims `{sub, kind="worker", pool_id, exp}`). Worker-JWT verification middleware. |
| `services/orchestration/api/workers.py` | FastAPI router: `POST /v1/workers/register`, `WS /v1/workers/channel`. |
| `services/orchestration/shared/uri_validation.py` (extracted) | `_validate_artifact_uri` + `_sanitize_config` moved out of soon-deleted `llmd/spec_builder.py`. All consumers updated. |
| `infra/schema/migrations/NNNN_worker_deployments.sql` | `ALTER TABLE compute_nodes ADD COLUMN kind text NOT NULL DEFAULT 'unknown';` plus a partial index on `kind='worker'`. |
| `services/orchestration/services/model_deployment/...` | New branch: when `compute_nodes.kind == 'worker'` (or, equivalently, deployment is targeted at a worker-kind node), call `worker_controller.load_model(...)` instead of `llmd_runtime.deploy(...)`. |

### 4.3 What's deleted vs. kept

| Path | Decision | Reason |
|---|---|---|
| `services/orchestration/services/node-agent/` | **Delete** | Replaced by Go worker's heartbeat. |
| `services/orchestration/services/llmd_runtime/` | **Delete** | k8s/llm-d deployment path abandoned. |
| `services/orchestration/services/llmd/spec_builder.py` | **Delete** | Same; validators extracted first. |
| `services/orchestration/proto/v1/compute_node.proto` + generated stubs | **Delete (MVP)** | Heartbeats flip to JSON over WS; gRPC server endpoint no longer needed. (Re-add later only if we re-introduce non-worker direct gRPC clients.) |
| `services/orchestration/services/compute_node/service.py` | **Delete** | Functionality (state-machine transitions, `inventory_repo.mark_ready`, `update_usage`) is moved into `worker_controller` and called from the WS handler. |
| `services/orchestration/services/adapter_engine/adapters/akash/*` | **Untouched** | Out of scope. |
| `services/orchestration/services/adapter_engine/adapters/nosana/*` | **Untouched** | Out of scope. Its `job_builder.py` recipes are the reference for the worker's Go recipes. |
| `services/orchestration/services/depin-sidecar/*` | **Untouched** | Out of scope. |
| `services/inference/core/handlers/*` | **Untouched** | Already reads `deployment["endpoint"]`. New `endpoint` source for worker-kind deployments is set by `worker_controller` on `CommandResult{status: "ok"}`. |
| `services/inference/core/providers/engines/*` | **Untouched** | Outbound HTTP adapter, control-plane-side. |

## 5. Protocol

### 5.1 Bootstrap (once per worker, or after token loss)

```
POST /v1/workers/register
  Authorization: Bearer <BOOTSTRAP_TOKEN>
  Content-Type: application/json
  {
    "node_name":      "<string, 1..255>",
    "pool_id":        "<uuid>",
    "advertise_url":  "https://gpu-host-7.example.com:8443",
    "allocatable": {
      "cpu":        "<int cores>",
      "memory":     "<int bytes>",
      "gpu":        "<int count>",
      "gpu_models": ["NVIDIA A100-SXM4-80GB", ...]
    }
  }

200 OK
  { "node_id": "<uuid>", "worker_jwt": "<jwt, exp=30d>" }
```

- Bootstrap-token claims include `scope: "worker:bootstrap"` and `exp ≤ 24h`. Signed with `JWT_SECRET_KEY`.
- On success, CP inserts/updates `compute_nodes` row with `kind='worker'`, `state='provisioning'`, sets `allocatable` columns.
- On `(pool_id, node_name)` conflict → 409 with explanatory body.
- Worker persists `worker_jwt` to `/var/lib/inferia-worker/token` (mounted volume in compose). Subsequent boots skip bootstrap if token exists and is unexpired.

### 5.2 Control channel

```
GET /v1/workers/channel  (WebSocket upgrade)
  Authorization: Bearer <worker_jwt>
```

CP verifies JWT, looks up `node_id`, transitions state `provisioning → ready` if needed, registers connection. Sends `Hello`. Worker starts 5-second heartbeat ticker and command dispatch loop.

Envelope: `{type, id, ts, body}` where `id` is a UUIDv4 for command/response correlation.

| `type` | direction | `body` | response |
|---|---|---|---|
| `Hello` | CP → W | `{server_time, channel_id}` | none |
| `Heartbeat` | W → CP | `{used: {cpu_pct, mem_used, gpu_used[]}, loaded_models: [deployment_id], events?: [{type:"ModelExited", deployment_id, exit_code, reason}]}` | none |
| `LoadModel` | CP → W | `{deployment_id, recipe, model: {artifact_uri, format, backend}, config: {…}, gpu_indices: [int], port}` | `CommandResult` |
| `UnloadModel` | CP → W | `{deployment_id}` | `CommandResult` |
| `CommandResult` | W → CP | `{in_reply_to, status: "ok"|"failed", detail?, endpoint_url?}` | none |
| `Ping` | CP → W | `{}` | none |

### 5.3 Idempotency & reconnect

- Worker dedupes commands by `id` for 5 minutes. Replay returns the cached `CommandResult`.
- WS reconnect: exponential backoff 1s → 2s → 4s → … cap 30s, jittered.
- On reconnect, first `Heartbeat` carries the *actual current* `loaded_models`. CP reconciles by issuing `UnloadModel` for unexpected and `LoadModel` for missing.
- CP marks node `unreachable` after **3 missed heartbeats (≈15s)** — endpoints withdrawn from inference routing; loaded_models rows kept for reconcile on reconnect.

### 5.4 Inference path

```
client → InferiaLLM /v1/chat/completions
      → api_gateway auth/RBAC
      → inference handler reads deployment.endpoint
      → HTTP POST to https://<worker_advertise>:<port>/v1/chat/completions
        with Authorization: Bearer <INFERENCE_TOKEN>
      → worker's Fiber router proxies to 127.0.0.1:<model_port>
      → SSE streams back through worker → CP → client
```

Worker proxies (rather than CP hitting model container directly) so the inference port is one stable port per worker, the worker enforces shared inference-traffic auth, and model containers stay on a private docker network.

## 6. Worker-side model state machine

```
absent → pulling → starting → running ↔ degraded
   ↑         ↓         ↓          ↓
   └─────── failed ←──┘    stopping → absent
```

- `pulling`: `docker pull` in progress.
- `starting`: container running, readiness probe pending.
- `running`: readiness probe passed; included in `loaded_models` heartbeat.
- `degraded`: 3 consecutive readiness-probe failures during steady state. Still in `loaded_models` but `ModelExited` emitted if the container exits.
- `failed`: terminal in worker memory; `docker rm` complete. Reported once via `CommandResult` then forgotten.

Transitions to `running`, `failed`, `absent` produce a `CommandResult`. Steady-state health is implicit in heartbeat membership. Mid-life crashes produce a `ModelExited` event in the next heartbeat.

## 7. Failure handling

| Failure | Worker action | CP action |
|---|---|---|
| Bootstrap 401 | Log + exit non-zero (compose restarts) | none |
| Worker JWT expired | Delete token file, retry full bootstrap | none |
| CP unreachable | Exponential backoff reconnect; keep running models | After 3 missed heartbeats: node `unreachable`, withdraw endpoints from routing |
| Docker socket missing | Log + exit non-zero at startup | n/a |
| `docker pull` fails | `CommandResult{failed, detail}` — no partial container | Mark deployment `failed`, surface in UI |
| Readiness probe times out | Stop + rm container, `CommandResult{failed}` | as above |
| Model container exits while running | Emit `ModelExited` in next heartbeat; `docker rm` | Mark deployment `crashed`; scheduler may re-place |
| Worker process crashes | compose `restart: unless-stopped` brings it back; reconcile via `loaded_models` | Heartbeats resume; reconcile diff |
| Host reboots, containers gone | First heartbeat shows empty `loaded_models`; CP re-issues `LoadModel` | same |
| Duplicate `(pool_id, node_name)` | Receive 409 at register | Reject; operator changes `NODE_NAME` |
| nvidia-smi missing / no GPU | `gpu_used: []`; node usable for CPU-only deployments | scheduler skips for GPU placements |
| Inference request for not-yet-`running` deployment | 503 + `Retry-After: 5` | inference handler retries / surfaces error |
| Slow pull (multi-GB image) | Per-recipe `pull_timeout`, default 600s | n/a |
| Wrong `WORKER_ADVERTISE_URL` | n/a — worker doesn't know | Endpoint fails health probes; surface in UI |

## 8. Security

- **Control-channel auth:** worker-JWT signed with shared `JWT_SECRET_KEY`. Claims include `kind: "worker"`. Existing user-auth middleware rejects `kind != "user"` tokens, so worker tokens cannot be reused as user tokens; new worker middleware enforces `kind == "worker"`.
- **Inference-channel auth:** `Authorization: Bearer <INFERENCE_TOKEN>` required on inbound `/v1/*`. Constant-time compare in worker (`subtle.ConstantTimeCompare`).
- **Model containers isolated:** dedicated docker network `inferia-models`; ports bound to `127.0.0.1:<port>` only, not exposed to LAN.
- **Recipe input sanitisation:** `artifact_uri` validated against allowlisted URI schemes (`s3`, `gs`, `hf`, `http`, `https`, `oci`) and a no-shell-metacharacters regex, both on CP side (mints the command) and worker side (defensive re-validation before `docker run`). Config map filtered to `_ALLOWED_CONFIG_KEYS` (current set from `spec_builder.py`).
- **No shell concatenation:** docker invocations use the Go Docker SDK structs, never string-built shell. Recipe CLI args are typed.
- **Bootstrap token lifetime:** scope `worker:bootstrap`, max 24h, not single-use in MVP (accepted risk; rotate via env).

## 9. Configuration

### 9.1 Worker (env vars)

| Var | Required | Default | Notes |
|---|---|---|---|
| `CONTROL_PLANE_URL` | yes | — | `https://control.example.com` |
| `BOOTSTRAP_TOKEN` | yes (first boot) | — | scope `worker:bootstrap` |
| `NODE_NAME` | yes | — | unique within pool |
| `POOL_ID` | yes | — | uuid of the pool |
| `WORKER_ADVERTISE_URL` | yes | — | URL CP will use for inference |
| `WORKER_LISTEN_ADDR` | no | `0.0.0.0:8080` | Fiber bind addr |
| `INFERENCE_TOKEN` | yes | — | shared with CP `WORKER_INFERENCE_TOKEN` |
| `TOKEN_FILE` | no | `/var/lib/inferia-worker/token` | worker-JWT persistence path |
| `DOCKER_HOST` | no | `unix:///var/run/docker.sock` | |
| `MODELS_NETWORK` | no | `inferia-models` | docker network for model containers |
| `LOG_LEVEL` | no | `info` | `debug|info|warn|error` |
| `PULL_TIMEOUT_SECONDS` | no | `600` | per-load default |
| `READINESS_TIMEOUT_SECONDS` | no | `180` | per-load default |

### 9.2 InferiaLLM (env vars added)

| Var | Required | Notes |
|---|---|---|
| `WORKER_INFERENCE_TOKEN` | yes | injected into inference gateway when calling worker endpoints |

`JWT_SECRET_KEY` is already required.

## 10. Docker compose (in `inferia-worker` repo)

```yaml
services:
  worker:
    image: inferiaai/inferia-worker:latest
    restart: unless-stopped
    network_mode: host        # or: ports + dedicated network; default host for direct GPU box
    runtime: nvidia
    environment:
      CONTROL_PLANE_URL:    ${CONTROL_PLANE_URL}
      BOOTSTRAP_TOKEN:      ${BOOTSTRAP_TOKEN}
      NODE_NAME:            ${NODE_NAME}
      POOL_ID:              ${POOL_ID}
      WORKER_ADVERTISE_URL: ${WORKER_ADVERTISE_URL}
      INFERENCE_TOKEN:      ${INFERENCE_TOKEN}
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
      - worker-state:/var/lib/inferia-worker
volumes:
  worker-state:
```

`.env.sample` in the repo lists each variable with a comment. Operator copies to `.env`, fills values from the InferiaLLM admin UI (which displays a generated bootstrap token + pool id), and runs `docker compose up`.

## 11. Testing

User instruction: 95%+ coverage on new/modified packages, including overflow / edge cases.

### 11.1 Worker (Go) — unit tests

| Package | Target | Notable cases |
|---|---|---|
| `internal/config` | 100% | Missing env, malformed URL, `NODE_NAME` length boundary (1, 255, 256), invalid `BOOTSTRAP_TOKEN`, scheme check on `WORKER_ADVERTISE_URL`, log-level enum |
| `internal/telemetry/cpu` | 100% | `/proc/stat` parse, malformed line tolerated, zero-cpu host |
| `internal/telemetry/memory` | 100% | `/proc/meminfo` parse, missing keys, overflow >2^63 clamp |
| `internal/telemetry/gpu` | 100% | nvidia-smi csv parse, empty (no GPUs), partial line, comma-decimal locale, non-zero exit → empty slice |
| `internal/control` | ≥95% | Register happy + 401 + 409, token persisted & reused, reconnect backoff cap 30s, command dedup on replay, heartbeat timing |
| `internal/runtime/recipes` | 100% | Each recipe roundtrips its spec, invalid name rejected, oversized config filtered, URI scheme allowlist enforced |
| `internal/runtime` | ≥90% | LoadModel happy (fake dockerclient), pull failure no leak, readiness timeout cleans up, double-LoadModel idempotent, UnloadModel on absent ok, crash detection emits `ModelExited` |
| `internal/runtime/dockerclient` | arg-construction tests; runtime exercise via integration | image name not interpolated, `--gpus device=N` literal |
| `internal/inference` | ≥95% | Proxy preserves headers, SSE chunk-by-chunk, unknown deployment id → 503 + Retry-After, missing/invalid `INFERENCE_TOKEN` → 401, body size limit |
| `internal/auth` | 100% | Token store thread-safe under contention (`-race`), constant-time compare verified |

### 11.2 Worker — integration tests (build tag `integration`)

- Real docker pull/run/stop using a stub container (`nginx:alpine` configured with `/health`).
- End-to-end via `dockertest`: worker binary + stub CP (`httptest` WS) + stub model container → `LoadModel` → inference 200 → `UnloadModel` → 503.
- Reconnect under chaos: stub CP closes WS mid-stream; assert backoff + replay + no duplicate `LoadModel`.

### 11.3 InferiaLLM (Python) — pytest

| Module | Cases |
|---|---|
| `worker_controller/*` | Register validates token, expired rejected, JWT claims correct, user JWT rejected as worker, duplicate name → 409, state machine `provisioning → ready` on first WS connect |
| `worker_controller/auth.py` | JWT roundtrip, tampered claims rejected, expiry honoured, wrong `kind` rejected by middleware |
| `api/workers.py` | `/register` happy + auth failures; WS auth; concurrent connects for same node serialise (second supersedes; first closed cleanly) |
| `shared/uri_validation.py` | All existing `_validate_artifact_uri` + `_sanitize_config` tests migrated verbatim |
| `model_deployment` | Branch on `kind=='worker'` → calls `worker_controller.load_model`; failure path; idempotent retries |
| Inventory state machine | `mark_ready` works via new path; `unreachable` after 3 misses |
| Migration | Applies + rolls back cleanly |

### 11.4 Cross-system contract test

A test file in InferiaLLM repo spins up real CP + real worker container + drives one full deployment lifecycle. Required-green in CI. Gate: `docker compose up && wait-ready.sh && docker compose down`, worker reaches `ready` within 30s against a stubbed CP.

### 11.5 Coverage gates

- Worker: `go test -coverprofile -covermode=atomic ./...`, total ≥ 95%.
- InferiaLLM: `pytest --cov=inferia.services.orchestration.services.worker_controller --cov=inferia.services.orchestration.api.workers --cov=inferia.services.orchestration.shared.uri_validation --cov-fail-under=95`.

### 11.6 Edge-case checklist

- Tokens: 0, 1, 4096, 65536 bytes — accept ≤4096, reject larger with 413.
- Node names: empty, 1, 255, 256 chars, unicode, names containing `..`, `/`, null byte.
- `loaded_models` in heartbeat: 0, 1, 1000 entries.
- Recipe config maps: empty, 64 keys (max), key length 128 (max), oversized rejected.
- Concurrent commands: 100 in-flight `LoadModel` — serialised in worker, not interleaved on docker; CP retries idempotent.
- WS frames >1MB rejected on both ends.
- Inference body: 0, 1, 10MB (limit), 10MB+1 (reject).
- Password / token length overflow tests (`BOOTSTRAP_TOKEN`, `INFERENCE_TOKEN`).

## 12. Migration & rollout

1. Land the worker repo + control-plane additions on `feat/inferia-worker-extraction` behind the existing deployment-`kind` switch. Existing deployments continue with their current `kind`; nothing changes for them.
2. Delete `node-agent/`, `llmd_runtime/`, `llmd/spec_builder.py`, `compute_node/service.py`, and the `compute_node.proto` generated stubs in the **same PR** — they have no users once the new path is in.
3. Schema migration is forward-only: `ALTER TABLE compute_nodes ADD COLUMN kind text NOT NULL DEFAULT 'unknown';` plus a partial index. Existing rows get `kind='unknown'` (no behavioural change — they were not workers and the worker path filters on `kind='worker'`). New `worker_controller.register` writes `kind='worker'` explicitly. No backfill needed.
4. Operator-facing rollout doc: `docs/operator/inferia-worker-quickstart.md` (a follow-up doc PR, not blocking this branch).

## 13. Open questions resolved during brainstorming

- **Local runtime invocation:** Docker-out-of-Docker, reusing recipes from `nosana/job_builder.py`. Not host-subprocess (breaks "compose up only"), not pluggable (no second use case justifying it).
- **Wire protocol:** HTTPS REST for register; persistent WebSocket for the control channel; direct HTTPS for inference. Not gRPC (avoid protoc tooling in worker build), not pure REST polling (latency).
- **Inference reachability:** direct port; worker advertises `WORKER_ADVERTISE_URL`. Not WS reverse-tunnel (complicates SSE streaming for v1; can be added later for NAT'd hosts).
- **Auth:** bootstrap token → long-lived worker JWT.
- **Repo layout:** separate git repo at `/storage/intern/hooman/work/inferia-worker`.
- **Scope of DePIN (Nosana, Akash) and llm-d/k8s paths:** DePIN untouched. llm-d/k8s path removed in this iteration.

## 14. Non-goals / explicit YAGNI

- Multi-tenancy isolation between models on the same worker beyond the docker network boundary.
- Custom autoscaling decisions from the worker (autoscaler keeps its current signals).
- Worker-initiated model artifact prefetching / cache management.
- Live model migration between workers.
- Multi-GPU tensor-parallel per single replica (recipe contract accepts `gpu_indices` so this is a forward-compatible addition).

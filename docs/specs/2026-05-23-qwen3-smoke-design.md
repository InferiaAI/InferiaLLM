# Qwen3-0.6B end-to-end smoke (local worker + AWS EC2) — design spec

- **Date:** 2026-05-23
- **Repos:** InferiaLLM (`feat/aws-ec2-provisioning`), inferia-worker (`feat/aws-ec2-bootstrap`)
- **Status:** brainstorming complete — pending user review before plan
- **Approach:** A — Verify + minimal patches (no new product surface)

## 1. Goal

Prove that the existing `provider → compute pool → node → deployment → sandbox` flow works end-to-end for **both** node types and **both** model runtimes:

- Local worker node (manual `docker compose up -d` on this dev box, "worker" provider type).
- AWS EC2 worker node (Pulumi-provisioned via the existing "aws" provider type).
- Each node loads `Qwen3-0.6B` under both the vLLM recipe and the Ollama recipe.
- The dashboard's Sandbox chats with the deployed model.

The deliverable is **repeatable smoke verification**, not a new feature. Two bash/Python smoke scripts (`local`, `aws`) drive the backend; one Playwright spec drives the UI for the local scenario. Any defects found during execution are fixed in line with this design — anything beyond that becomes follow-up.

## 2. Non-goals

- New provider types (no "local" auto-provisioner; the manual docker-compose flow is fine).
- New dashboard pages or settings UI.
- Schema migrations or RBAC changes.
- Multi-replica control-plane support — single-process orchestration is assumed.
- HuggingFace rate-limit or GHCR throttling resilience beyond surfacing errors verbatim.
- Operator playbook documentation (the smoke scripts + Playwright spec are the executable docs).
- Approach B's in-dashboard "Smoke" page — deferred.

## 3. Scope summary

### 3.1 inferia-worker (`feat/aws-ec2-bootstrap`)

1. New `internal/runtime/ollama_pull.go` — after the Ollama container passes its readiness probe, POST `/api/pull` for the configured model. The deployment stays in `StateStarting` until the pull completes, then transitions to `StateRunning`. No new substate; existing UI shows "Starting" throughout, which is accurate.
2. Edit `internal/runtime/runtime.go` — call `ollamaPullIfNeeded` after readiness probe success when the recipe name has the `ollama` prefix.
3. New `internal/runtime/ollama_pull_test.go` — ≥95% line+branch coverage including length-overflow, shell-metacharacter, and concurrency edge cases (see §7.1).

### 3.2 InferiaLLM (`feat/aws-ec2-provisioning`)

4. New `scripts/smoke/__init__.py`, `scripts/smoke/lib.py` — typed httpx API client, polling helpers, chat verifier, cost printer.
5. New `scripts/smoke/local.py` — local end-to-end orchestrator.
6. New `scripts/smoke/aws.py` — AWS end-to-end orchestrator with `trap`/`try-finally` teardown.
7. New `scripts/smoke/test_lib.py` — pytest with `respx` mocks; ≥95% coverage on `lib.py`.
8. New `deploy/compose.worker-local.yml` — sibling worker compose joining the unified stack network.
9. Edit `Makefile` — add `smoke-local`, `smoke-local-up`, `smoke-local-down`, `smoke-aws`, `smoke-aws-dry`.
10. New `apps/dashboard/playwright/e2e/qwen3-local-smoke.spec.ts` — UI walkthrough for the local scenario only.
11. This spec doc + the corresponding plan in `docs/plans/`.

### 3.3 inferia-worker CI

12. Edit `.github/workflows/docker-publish.yml` — `workflow_dispatch` already exists; add a `tag_suffix` input and route it through the docker/metadata-action so a manual trigger publishes `ghcr.io/<owner>/inferia-worker:<tag_suffix>`. Existing `v*`-tag trigger and `latest` semantics stay untouched.

## 4. Architecture

```
┌──────────────── this dev box (linux/amd64, RTX 4050 6 GB) ──────────────┐
│                                                                          │
│  InferiaLLM unified compose (control plane)                              │
│  ├─ gateway        :8000   user-JWT, sandbox proxy                       │
│  ├─ orchestration  :8080   bootstrap tokens, deployment strategies       │
│  ├─ inference      :8001   chat-completions → worker (bearer-token auth) │
│  ├─ postgres / redis / es / logstash                                     │
│  └─ network: deploy_inferia-net                                          │
│                                                                          │
│  inferia-worker (sibling compose, same network)                          │
│  ├─ container: inferia-worker                                            │
│  ├─ CONTROL_PLANE_URL = http://gateway:8000                              │
│  ├─ WORKER_ADVERTISE_URL = http://inferia-worker:8080                    │
│  ├─ sibling model containers on `inferia-models` bridge                  │
│  │   ├─ inferia-ollama-<id>   (Ollama, sequential)                       │
│  │   └─ inferia-vllm-<id>     (vLLM,  sequential)                        │
│  └─ image: locally built `inferia-worker:smoke` from feat/aws-ec2-bootstrap
│                                                                          │
└──────────────────────────────────────────────────────────────────────────┘
                                  ▲
                                  │ "worker" pool (manual)
                                  │
                          scripts/smoke/local.py + Playwright


┌────────────────────────── AWS (per smoke run) ────────────────────────────┐
│                                                                            │
│  EC2 g4dn.xlarge (T4 16 GB) — tagged InferiaSmoke=true                     │
│  ├─ cloud-init                                                             │
│  │   ├─ install docker + nvidia-container-toolkit                          │
│  │   └─ docker run ghcr.io/<owner>/inferia-worker:smoke-<ts>               │
│  └─ inferia-worker registers with control plane via public URL             │
│      and serves /v1/* through INFERENCE_TOKEN bearer auth                  │
│                                                                            │
│  Pulumi stack (state on control-plane container persistent volume)         │
│                                                                            │
└────────────────────────────────────────────────────────────────────────────┘
                                  ▲
                                  │ "aws" pool (PulumiAWSAdapter)
                                  │
                          scripts/smoke/aws.py
```

## 5. Components

### 5.1 Worker — Ollama pull-after-ready (`internal/runtime/ollama_pull.go`)

```go
// ollamaPullIfNeeded POSTs /api/pull to the local Ollama instance after the
// container passes its readiness probe. No-op for non-Ollama recipes.
// Bounded by Runtime.cfg.PullTimeout.
func (r *Runtime) ollamaPullIfNeeded(ctx context.Context, d *deployment) error
```

Behavior:
- Trigger: recipe name has prefix `ollama` (`ollama`, `ollama-omni` if added later).
- Source of model name: `d.plan.Env["INFERIA_OLLAMA_MODEL"]` (already set by `ollamaRecipe.BuildPlan`).
- HTTP request: `POST http://<endpoint>/api/pull` with body `{"name": "<model>", "stream": false}`. Honors streaming NDJSON responses too — reads until the final line, requires `status:"success"`.
- Retry: one retry on 5xx; no retry on 4xx; no retry on unrecognized response shape.
- On error: container is stopped + removed (existing cleanup path), deployment marked `StateFailed`.
- Input validation: model name length ≤ 256 (Ollama tag limit is 128, leave headroom); reject NUL or shell-metacharacters (`;|$` backtick) before the HTTP call.

State machine integration:

```
absent ── pull image ──► pulling ── start container ──► starting ── readiness probe ──┐
                                                                                       │
                                                                                       ▼
                                                                         recipe == "ollama"?
                                                                           │ no            │ yes
                                                                           ▼               ▼
                                                                     StateRunning   /api/pull
                                                                                          │ success
                                                                                          ▼
                                                                                     StateRunning
                                                                                          │ failure
                                                                                          ▼
                                                                              cleanup → StateFailed
```

No new state values are introduced — the deployment remains in `StateStarting` for the duration of the pull, and the existing UI continues to show "Starting" (which is accurate). The pull happens inside `LoadModel`'s synchronous code path, so `CommandResult` is only sent once the model is fully available.

### 5.2 InferiaLLM — `scripts/smoke/lib.py`

Public surface:

```python
class SmokeAPI:
    def __init__(self, base_url: str, token: str | None = None): ...
    def login(self, email: str, password: str) -> None: ...
    def create_pool(self, *, provider: str, name: str,
                    instance_type: str | None = None,
                    metadata: dict | None = None) -> str: ...        # → pool_id
    def destroy_pool(self, pool_id: str) -> None: ...                # idempotent
    def mint_bootstrap_token(self, pool_id: str, ttl_hours: int) -> dict: ...
    def list_workers(self, pool_id: str) -> list[dict]: ...
    def create_deployment(self, *, pool_id: str, recipe: str,
                          model_uri: str, name: str,
                          config: dict | None = None) -> str: ...    # → deployment_id
    def delete_deployment(self, deployment_id: str) -> None: ...
    def chat(self, deployment_id: str, prompt: str,
             stream: bool = False, timeout: float = 60.0) -> str: ... # → assistant content

def wait_until(predicate: Callable[[], T | None], *,
               timeout: float, interval: float = 2.0,
               tolerate_status: set[int] = {503, 504}) -> T: ...

def cost_estimate(instance_type: str, hours: float) -> str: ...
```

Implementation:
- httpx.Client with timeout=30 s; one auto-refresh on 401.
- `wait_until` polls; 4xx propagates immediately; 5xx in `tolerate_status` is retried.
- `chat` posts to `/v1/inference/chat/completions`; supports SSE stream parsing (concatenates `delta.content` chunks, requires `[DONE]`).
- All raises are typed: `SmokeTimeoutError`, `EmptyResponseError`, `StreamTruncatedError`, `APIError`.

### 5.3 InferiaLLM — `scripts/smoke/local.py`

```
python -m scripts.smoke.local [--keep-on-fail] [--engines=ollama,vllm]
```

Steps (as in design §2.1):

1. Verify the unified stack is up and reachable; abort with `make smoke-local-up` hint otherwise. Verify the worker image `inferia-worker:smoke` exists locally; abort with build hint if not.
2. Verify no `inferia-worker` container is already running. Abort if yes (prevents collision with a half-finished prior run).
3. Login → admin JWT.
4. Create pool `smoke-local-<rand>` of provider `worker`.
5. Mint bootstrap token (TTL 1h).
6. `docker compose -f deploy/compose.worker-local.yml up -d` with `BOOTSTRAP_TOKEN`, `POOL_ID`, `NODE_NAME`, `INFERENCE_TOKEN` set in env. (No crash-loop because the token is real on first up.)
7. Wait for 1 worker `ready` in pool (≤ 60 s).
8. For each engine in order (default `ollama,vllm`):
   - Create deployment `smoke-<engine>` with the right URI and config.
   - For vLLM: `gpu_memory_utilization=0.5, max_model_len=4096, dtype=bfloat16`.
   - Wait for `StateRunning` (≤ 180 s for ollama, ≤ 300 s for vllm).
   - Chat `"Say hello in one short sentence."`, assert non-empty.
   - Delete deployment, wait for VRAM release (≤ 60 s).
9. `docker compose -f deploy/compose.worker-local.yml down -v`.
10. Delete pool. Exit 0.

`--keep-on-fail`: skip steps 9-10 if any step fails (for debugging).

### 5.4 InferiaLLM — `scripts/smoke/aws.py`

```
python -m scripts.smoke.aws [--instance-type=g4dn.xlarge] [--region=<from-provider>]
                            [--worker-image-tag=smoke-<ts>] [--keep-on-fail]
```

Steps (as in design §2.2):

1. Pre-flight: AWS provider configured? subnet/SG present? Any pre-existing `smoke-aws-*` pool? (abort if yes — operator must destroy manually).
2. Cost-print + 5 s Ctrl-C window unless `SMOKE_NO_CONFIRM=1`.
3. Trigger GHCR build via `gh workflow run docker-publish.yml -f tag_suffix=smoke-<ts>`; wait for completion; record `worker_image_tag=smoke-<ts>`. (Skipped if `--worker-image-tag` passed explicitly.)
4. Install bash + python teardown traps **before** any AWS mutation.
5. Create pool `smoke-aws-<ts>` of provider `aws` with `instance_type`, `region`, `metadata.worker_image_tag`.
6. Wait for `pulumi_state=succeeded` (≤ 300 s).
7. Wait for 1 worker `ready` (≤ 180 s).
8. For each engine sequentially (mirrors local; simpler failure attribution on T4 16 GB):
   - Deploy, wait running, chat, undeploy.
9. Destroy pool. Verify no EC2 instances tagged `InferiaPoolId=<id>` remain `running`/`pending`.
10. Hard wall-clock guard at 20 min — `timeout 1200` in the Make target.

### 5.5 InferiaLLM — `deploy/compose.worker-local.yml`

Started by `scripts/smoke/local.py` *after* a real bootstrap token has been minted. All required env vars are non-defaulted so docker-compose refuses to start without them — fail-fast.

```yaml
services:
  worker:
    image: inferia-worker:smoke
    container_name: inferia-worker
    restart: unless-stopped
    environment:
      CONTROL_PLANE_URL:    http://gateway:8000
      BOOTSTRAP_TOKEN:      ${BOOTSTRAP_TOKEN:?required}
      POOL_ID:              ${POOL_ID:?required}
      NODE_NAME:            ${NODE_NAME:-smoke-local-1}
      WORKER_ADVERTISE_URL: http://inferia-worker:8080
      INFERENCE_TOKEN:      ${INFERENCE_TOKEN:?required}
      MODELS_NETWORK:       inferia-models
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:rw
      - worker-state-local:/var/lib/inferia-worker
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: all
              capabilities: [gpu]
    networks: [default, inferia-cp]

volumes:
  worker-state-local:

networks:
  inferia-cp:
    name: deploy_inferia-net
    external: true
```

The worker doesn't *need* GPUs itself, but exposing them lets it pass `--gpus=all` through to sibling containers without host-side gymnastics.

### 5.6 InferiaLLM — Makefile additions

```makefile
smoke-local-up:    ## bring up unified stack + build worker image (no worker container yet)
	docker compose -f deploy/docker-compose.unified.yml up -d
	docker build -t inferia-worker:smoke ../inferia-worker

smoke-local-down:  ## tear down everything (worker compose + unified)
	-docker compose -f deploy/compose.worker-local.yml down -v
	docker compose -f deploy/docker-compose.unified.yml down

smoke-local: smoke-local-up
	python -m scripts.smoke.local

smoke-aws-dry:     ## pre-flight only; no AWS spend
	python -m scripts.smoke.aws --dry-run

smoke-aws:         ## real EC2; hard 20-min wall clock
	timeout 1200 python -m scripts.smoke.aws --instance-type=g4dn.xlarge
```

`smoke-local-up` deliberately does **not** start the worker container — the smoke script does that after minting a real bootstrap token, so the worker never sees a placeholder value.

### 5.7 Playwright spec — `qwen3-local-smoke.spec.ts`

As detailed in design §2.3. Local scenario only. Skipped in CI by default; runs locally with `npx playwright test --grep qwen3-local-smoke`.

The spec uses a `globalSetup` fixture written in TypeScript that:
1. Calls the orchestration API directly (with the same admin JWT) to mint the bootstrap token.
2. Uses `child_process.execSync` to `docker compose -f deploy/compose.worker-local.yml up -d` with the env it just minted.
3. Polls the API until the worker shows `ready` (≤ 60 s).

After that, the test exercises the UI from login through Sandbox chat, asserting on visible state. The teardown fixture brings the worker compose down. The fixture intentionally bypasses the UI's "Add Worker" modal — the Playwright test focuses on Pool → Deployment → Sandbox; AddWorkerModal already has its own component tests in the dashboard suite.

### 5.8 GHCR workflow — `workflow_dispatch` input

`workflow_dispatch` already exists in `.github/workflows/docker-publish.yml` (no inputs). Extend it with a `tag_suffix` input and route it through `docker/metadata-action`:

```yaml
on:
  push:
    tags: ['v*']
  workflow_dispatch:
    inputs:
      tag_suffix:
        description: 'tag for one-off build (e.g. smoke-1748023456)'
        required: false
        type: string

# in the metadata step:
tags: |
  type=semver,pattern={{version}}
  type=semver,pattern={{major}}.{{minor}}
  type=raw,value=latest,enable=${{ github.ref_type == 'tag' }}
  type=raw,value=${{ inputs.tag_suffix }},enable=${{ inputs.tag_suffix != '' }}
```

Manual-dispatch with no `tag_suffix` is left alone (behaves as today — no tag emitted from the raw rule). No new permissions needed; the existing `packages: write` covers it.

## 6. Data flow

### 6.1 Local scenario — happy path

```
make smoke-local-up            [unified stack up + worker image built; ~45 s]
   │
   ▼
scripts.smoke.local.login                                   ──► gateway:8000
   │
   ▼
create_pool(provider=worker)                                ──► orchestration:8080
   │
   ▼
mint_bootstrap_token(pool_id, ttl=1h)                       ──► orchestration:8080
   │
   ▼
docker compose -f compose.worker-local.yml up -d
  with BOOTSTRAP_TOKEN, POOL_ID, INFERENCE_TOKEN injected
   │
   ▼
list_workers(pool_id) until 1 ready  [≤ 60 s]
   │
   ▼
create_deployment(recipe=ollama, model=ollama://qwen3:0.6b) ──► orchestration:8080
   │                                                                 │
   │                                                                 ▼
   │                                                          WS LoadModel ──► worker
   │                                                                              │
   │                                                                              ▼
   │                                                                  docker pull, run
   │                                                                  readiness probe
   │                                                                  /api/pull qwen3:0.6b   ◄── NEW
   │                                                                              │
   │                                                                              ▼
   │                                                                  Heartbeat: loaded
   ▼
wait deployment.state == Running                            [≤ 180 s]
   │
   ▼
chat("say hello")                                           ──► inference:8001
                                                                   │
                                                                   ▼
                                                            worker /v1/chat/completions
                                                                   │
                                                                   ▼
                                                            local Ollama container
   │
   ▼ assert non-empty
delete_deployment                                           [VRAM freed]
   │
   ▼
create_deployment(recipe=vllm, model=hf://Qwen/Qwen3-0.6B,
                  config={gpu_memory_utilization: 0.5,
                          max_model_len: 4096, dtype: "bfloat16"})
   │
   ▼ (mirror steps above)
delete_deployment, destroy_pool                             exit 0
```

### 6.2 AWS scenario — happy path

```
preflight (provider creds, subnet/sg, no stale smoke pools)
   │
   ▼
gh workflow run docker-publish.yml -f tag_suffix=smoke-<ts>
   │ wait for completion
   ▼
install traps (bash trap EXIT, python try/finally)
   │
   ▼
create_pool(provider=aws, instance_type=g4dn.xlarge,
            metadata={worker_image_tag: smoke-<ts>, subnet_id, sg_ids, …})
   │
   ▼ (PulumiAWSAdapter.provision background task)
   │   ec2.Instance up, cloud-init runs, docker pull ghcr.io/...:smoke-<ts>
   │   worker registers via public URL
   ▼
wait pulumi_state == succeeded  [≤ 300 s]
wait list_workers ready         [≤ 180 s]
   │
   ▼
for engine in [ollama, vllm]:
    create_deployment → wait Running → chat → delete
   │
   ▼
destroy_pool                                                 (Pulumi destroy)
verify no instances remain                                   (boto3 describe-instances)
   │
   ▼
clear trap, exit 0
```

## 7. Error handling

### 7.1 Worker — Ollama pull-after-ready

Test matrix (`ollama_pull_test.go`):

| # | Case | Setup | Expected |
|---|---|---|---|
| 1 | Happy path | mock 200 `{status:"success"}` | StateRunning; no error |
| 2 | Streamed success | NDJSON, last line `success` | StateRunning; no error |
| 3 | Unknown model | 404 | wrapped error; one cleanup; no retry |
| 4 | Transient 5xx | 500 then 200 | one retry; success |
| 5 | Persistent 5xx | 500, 500 | fail; cleanup |
| 6 | Network error | server closed before call | wrapped error; cleanup |
| 7 | Pull timeout | sleep > PullTimeout | ctx.Err; cleanup |
| 8 | Empty model env | INFERIA_OLLAMA_MODEL unset | pre-HTTP error |
| 9 | Model name 257 chars | reject | pre-HTTP error |
| 10 | Model name with `;` `|` `$` backtick | reject | pre-HTTP error |
| 11 | Concurrent LoadModel same id | two goroutines | one pull, second blocks on init mutex |
| 12 | Recipe = vllm | n/a | no-op return nil |

Coverage ≥95% line+branch on the new file; no regression on `runtime.go`.

### 7.2 Smoke — local

- Compose-up failure → abort before any API call; print `docker compose logs`.
- Worker doesn't register within 60 s → tail `docker logs inferia-worker`; fail.
- Deployment stuck `pulling`/`starting` past timeout → tail worker logs; fail.
- Chat returns empty or non-200 → fail; on `--keep-on-fail` leave stack up.
- No silent retry on 4xx. `wait_until` only retries 5xx in `tolerate_status`.

### 7.3 Smoke — AWS (spend guard)

Six layers, defense in depth:

1. **Pre-flight reject** of any pre-existing `smoke-aws-*` pool. Prevents compound spend after a crash.
2. **Bash `trap`** installed before any AWS-mutating call. `EXIT|INT|TERM` → destroy pool.
3. **Python `try/finally`** mirror inside `scripts/smoke/aws.py` — covers exceptions the bash trap won't see (e.g., uncaught inside Python).
4. **Post-teardown boto3 verification**: `describe-instances` filtered by `tag:InferiaPoolId` must be empty (state `running`/`pending`). Non-empty → exit non-zero.
5. **Wall-clock guard**: `timeout 1200` in the Make target. SIGTERM triggers the trap.
6. **Cost printout + 5 s Ctrl-C window** before mutation.

### 7.4 Concurrency between scenarios

- Pool names suffixed with random or timestamp tokens. Two AWS smokes can in principle coexist (different pool ids) — but the GHCR workflow_dispatch is single-tag-per-run, so the second run picks its own `smoke-<ts>`.
- Local scenario uses a fixed container name (`inferia-worker`). Concurrent local smokes are unsupported and detected: script aborts if a container with that name already exists.

## 8. Testing

CLAUDE.md mandates ≥95% coverage on new code with length-overflow + edge-case tests.

| New code | Test file | Min coverage | Notes |
|---|---|---|---|
| `inferia-worker/internal/runtime/ollama_pull.go` | `ollama_pull_test.go` | 95% line+branch | httptest server; 12 cases above |
| `InferiaLLM/scripts/smoke/lib.py` | `scripts/smoke/test_lib.py` | 95% line+branch | respx mocks; auth refresh, SSE truncation, polling |
| `InferiaLLM/scripts/smoke/{local,aws}.py` | — | exempt | The scripts *are* tests; gated by smoke runs themselves |
| `apps/dashboard/playwright/e2e/qwen3-local-smoke.spec.ts` | — | exempt | Smoke test, not unit. Stability bounded by per-step timeouts. |

### Run sequence at implementation time

1. Worker patch + Go tests → `go test -race -coverprofile=cov ./internal/runtime/...` (≥95%).
2. Build worker image locally (`docker build -t inferia-worker:smoke ../inferia-worker`).
3. Smoke lib + pytest with respx (≥95%).
4. `make smoke-local` → fix bugs found; document non-obvious ones in InferiaLLM CLAUDE.md Mistakes Log.
5. Playwright spec → fix bugs found.
6. GHCR `workflow_dispatch` PR + run.
7. `make smoke-aws` against the real AWS account.
8. Sign all commits with `~/.ssh/id_ed25519_gh`; no Claude attribution; per-repo branch policy.

## 9. Assumptions

1. **Qwen3-0.6B URIs**: `ollama://qwen3:0.6b` and `hf://Qwen/Qwen3-0.6B`. vLLM 0.16.0 supports Qwen3 (no `trust_remote_code` needed).
2. **AWS provider pre-configured**: credentials, default region, subnet/SG/optional IAM profile in `compute_pools.metadata`. Smoke script aborts cleanly if not.
3. **Local compose network**: worker joins `deploy_inferia-net` directly; no `host.docker.internal`.
4. **Bootstrap token TTLs**: 1 h local, 6 h AWS (covers cold-start variance).
5. **Worker image source**: local smoke uses locally-built `inferia-worker:smoke`. AWS smoke uses `ghcr.io/<owner>/inferia-worker:smoke-<ts>` built via `workflow_dispatch`, where `<owner>` is whatever `github.repository_owner` resolves to for the worker repo.
6. **`gh` CLI authenticated** with `workflow:write` scope on the worker repo. Required to trigger the GHCR build from `scripts/smoke/aws.py`. If not authenticated, the smoke aborts with a clear `gh auth login --scopes workflow` hint.
7. **Sandbox**: untouched unless integration uncovers a bug. Both engines expose OpenAI-compatible `/v1/chat/completions`; existing inference proxy should route correctly.
8. **`docker compose` plugin (not `docker-compose` v1)**: confirmed available on this dev box. Scripts call `docker compose`, not `docker-compose`.

## 10. What I will NOT do

- Push to remote — operator pushes.
- Open PRs.
- Modify `main` in either repo.
- Touch Settings → Providers UI, RBAC, model registry, or schema.
- Add a dashboard smoke runner (Approach B is deferred).

## 11. Open follow-ups

- An in-dashboard smoke runner (Approach B).
- A "local" auto-provisioner provider type (Approach C).
- vLLM Qwen3 with `gpu_memory_utilization=0.9` once a larger local GPU is available — current 6 GB forces 0.5 cap.
- S3-backed Pulumi state to unlock multi-replica control plane.

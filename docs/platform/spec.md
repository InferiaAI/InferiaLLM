# InferiaLLM Platform — Engineering Specification

**Status:** v0.3 (2026-11-02). CPU-verifiable simulator, data-plane and deployment foundations are implemented on the platform branch; GPU performance evidence remains explicitly pending calibration.
**Companion documents:** [`interfaces.md`](interfaces.md) (contracts between components) · [`adr/`](adr/README.md) (decision records) · `diagrams/` (the ten required diagrams) · `capacity/` (calibration and capacity report, Session 2).
**Evidence rule:** every number carries MEASURED, SIMULATED or EXTRAPOLATED ([ADR 0005](adr/0005-three-evidence-tiers.md)).

**Verification:** on 2026-11-02, all local code references and internal Markdown destinations in this document were checked. References to the simulator, llm-d adapter, and extension registry resolve in the respectively owned Session 2, 3, and 4 worktrees; they are not yet merged into this branch.

---

## 0. Traceability (brief → spec → workstream → status)

| Brief item | Spec section | Workstream (plan) | Status |
|---|---|---|---|
| §1 Complete architecture | §2 | WS1 | Skeleton |
| §1 Request lifecycle | §3 | WS1, WS2 | Skeleton |
| §1 Control plane vs data plane | §2.2 | WS1 | Drafted |
| §1 Orchestration, scheduling, routing, resources | §4, §5 | WS2, WS6 | In progress (llm-d mode built, S3 T2) |
| §1 Distributed inference across nodes | §6 | WS2, WS3 | TODO |
| §1 Horizontal scaling and fault tolerance | §7, §8 | WS7, WS3 | TODO |
| §1 Build in-house vs adopt | §2.3 | WS1 | Drafted ([ADR 0001](adr/0001-control-plane-builds-data-plane-adopts.md)) |
| §2 Ten diagrams | `diagrams/` | WS1 | Delivered (sources + SVGs) |
| §3 Component responsibilities | §2.1 | WS1 | Drafted |
| §3 Technology selection + justification | §2.4 | WS1 | Drafted |
| §3 API specifications | §9 | WS1, WS6 | Skeleton |
| §3 Data flow and protocols | §3.2 | WS1 | Drafted |
| §3 Database and metadata | §10 | WS1, WS9 | Skeleton |
| §3 Caching architecture | §11 | WS1 | Drafted |
| §3 Security boundaries | §12 | WS1, WS9 | Skeleton |
| §3 Failure scenarios and recovery | §8 | WS7 | TODO |
| §3 Bottlenecks and mitigations | §13 | WS5 | TODO (from capacity runs) |
| §3 Deployment architecture | §14 | WS8 | TODO |
| §3 Testing and benchmarking strategy | §15 | WS3, WS5 | Drafted |
| §4–5 Simulation engine, GPU/infra simulation | §16 | WS3 | In progress (S2 T1–T2 done) |
| §6 KV cache management | §5 | WS3, WS4 | In progress |
| §7 Load testing and capacity | `capacity/report.md` | WS5 | CPU baselines + KV sweep; GPU calibration pending |
| §8 Unified multimodal interface | §9.2 | WS6 | Skeleton |
| §9 Workload-specific scheduling | §4.3 | WS6 | Drafted ([ADR 0006](adr/0006-workload-classes-and-pools.md)) |
| §10 Reliability and fault tolerance | §8 | WS7 | TODO |
| §11 Self-hosted architecture | §14 | WS8 | TODO |
| §12 Enterprise controls | §12, §17 | WS9 | In progress (extension registry, S4 T1) |
| Part B Restructure readiness | §18 | — | Drafted |

---

## 1. Goals and non-goals

**Goals:** a self-hostable, production-grade inference platform that architecturally supports 10M–100M+ users across text, embeddings, audio, image and video workloads, built on mature open-source infrastructure, with credible capacity evidence.

**Non-goals:** writing our own inference engine, attention kernels or in-engine KV allocator ([ADR 0001](adr/0001-control-plane-builds-data-plane-adopts.md)); presenting simulated numbers as GPU performance ([ADR 0005](adr/0005-three-evidence-tiers.md)).

---

## 2. Architecture

### 2.1 Components and responsibilities

| Component | Code | Responsibility |
|---|---|---|
| Unified web app | `src/unified_web/app.py` | One port: `/api` → gateway, `/inf` → inference, `/v2` → OCI mirror, `/` → dashboard |
| API gateway (control plane) | `src/api_gateway/` | Auth (local / OIDC / inferia-auth), RBAC, policy, audit, management APIs, internal APIs for the data plane |
| Inference edge (data plane) | `src/inference/` | OpenAI/Anthropic-compatible API, auth-context and quota caching, rate and concurrency limits, routing to engines, streaming, usage events |
| Orchestration | `src/orchestration/` | Deployments, pools, placement, autoscaling, provisioning state machine, xDS for Envoy |
| Provider adapters | `src/providers/` | Kubernetes (incl. llm-d mode), AWS/Pulumi, Nosana, Akash, self-hosted worker |
| Inference router | llm-d EPP + Envoy (adopted) | Prefix-cache-aware, load-aware endpoint picking ([ADR 0002](adr/0002-primary-data-plane-k8s-llmd-vllm.md), [0008](adr/0008-raw-llmd-over-kserve.md)) |
| Engines | vLLM, SGLang, vLLM-Omni, InferiaDiffusion (adopted / sibling) | Continuous batching, paged KV, prefix caching, modality-specific execution |
| Simulator | `src/simulator/` (Session 2) | Capacity modelling ([ADR 0003](adr/0003-own-discrete-event-simulator.md)) |
| Dashboard | `apps/dashboard/` | Operator UI |
| Extension registry | `src/common/extensions/` (Session 4) | Plugin seams with open-source defaults (`interfaces.md` §6) |

### 2.2 Control plane vs data plane
- **Hot path (data plane):** inference edge → Envoy/EPP → engine. No synchronous Postgres writes per request; auth context and quota checks are cached (`src/inference/client.py` `context_cache`, `quota_check_cache`). Usage events append to Redis Streams with an HTTP fallback; the gateway batches, persists through `UsageSink`, then acknowledges. Pending events are redelivered after a worker restart.
- **Control plane (may be slow):** gateway management APIs, orchestration, provisioning, model registry, audit storage.
- **Boundary rule:** control-plane outages must not stop already-running deployments from serving.

### 2.3 Build vs adopt
See [ADR 0001](adr/0001-control-plane-builds-data-plane-adopts.md). In-house: API surface, tenancy and governance, admission/priority policy, multi-provider orchestration, recipes, simulator, benchmark harness, dashboard, packaging. Adopted: engines, in-engine KV, KV offload, inference routing, Kubernetes, observability stack, Postgres/Redis.

### 2.4 Technology selection
| Need | Choice | Main alternative | Why |
|---|---|---|---|
| Inference routing | llm-d v1.0.0 EPP (raw, rendered manifests) | KServe LLMInferenceService; Dynamo router | Kubernetes-native, precise prefix routing, CPU-testable; KServe pins older llm-d and replaces shared CRDs ([ADR 0008](adr/0008-raw-llmd-over-kserve.md)) |
| Engine | vLLM (pinned image in `engines.yaml`) | SGLang, TensorRT-LLM | Broadest model support, KV events for llm-d; SGLang/TRT-LLM via recipes |
| Disaggregated serving, KV tiers | vLLM offloading connector / LMCache; Dynamo as alternative | Dynamo KVBM | Works with llm-d today; Dynamo kept as documented backend |
| Multi-stage pipelines | Redis Streams job queue | Ray Serve | Avoids a second orchestrator next to Kubernetes ([ADR 0002](adr/0002-primary-data-plane-k8s-llmd-vllm.md)) |
| Capacity modelling | Own DES, calibrated | Vidur, Dynamo AIC | Needs users→cells→failures scope ([ADR 0003](adr/0003-own-discrete-event-simulator.md)) |
| Metadata | Postgres | — | Existing |
| Rate limits, queues, events | Redis | Kafka | Existing; Kafka optional at larger scale |

---

## 3. Request lifecycle

### 3.1 Steps (target)
1. Client → unified app `/inf/v1/...`.
2. Edge authenticates from cached context; strips client-supplied `x-inferia-*`, `x-llm-d-*`, `x-gateway-*` (`interfaces.md` §4).
3. Quota and rate checks (cached); workload class assigned by policy (`interfaces.md` §3); admission decides admit / queue / shed.
4. Edge forwards to the deployment endpoint: for llm-d deployments, the Envoy router Service on :8081 (`interfaces.md` §5), with objective / fairness headers.
5. EPP picks the endpoint by prefix-cache overlap and load; vLLM schedules (continuous batching, chunked prefill) and streams tokens.
6. Edge streams to the client; on disconnect, closes upstream so the engine aborts and the request is recorded as cancelled.
7. Edge emits a usage event asynchronously through Redis Streams (with an HTTP fallback when Redis is unavailable).

### 3.2 Protocols
HTTP/1.1 + SSE at the edge; HTTP to engines and routers; ZMQ for vLLM KV events to the EPP; WebSocket for the worker control channel and real-time audio; Redis Streams for deploy requests, usage events and jobs; gRPC inside orchestration.

---

## 4. Orchestration, scheduling and resource management
### 4.1 Deploy paths
Four provisioning routes (`deployment_server._provisioning_route`): `direct_adapter` (Kubernetes incl. llm-d mode, Nosana, Akash), `reconciler` (AWS/GCP/Azure via Pulumi), `self_register` (inferia-worker), and the legacy node-based strategies (`interfaces.md` §5).
### 4.2 Placement and autoscaling
The simulator models queue-depth, KV-utilisation and observed TTFT-p95 scale-out, cooldown, warm pools and cold/warm start delay. Production placement retains the existing provider-aware pool placement; measured control-plane autoscaling calibration remains pending.
### 4.3 Workload classes
Per-request classes select pool, queue policy, SLO and preemption ([ADR 0006](adr/0006-workload-classes-and-pools.md); `interfaces.md` §3). Mapped to llm-d `InferenceObjective` priorities and per-org fairness ids.

## 5. KV cache management
The simulator implements paged blocks, full-block prefix caching, eviction, recompute/swap preemption, explicit CPU/NVMe tiers and cache-aware routing. Its KV sweep is **SIMULATED** and uncalibrated; real vLLM saturation/offload figures remain a GPU-lease deliverable. The kind llm-d demonstration is **MEASURED platform routing behaviour** with simulated engine timing.

## 6. Distributed inference
Tensor parallel groups remain node-local. The simulator models prefill/decode replica roles and a configured KV transfer latency/bandwidth; a failure of either transfer endpoint after first token fails the request rather than duplicating output. Cross-node and real-engine transfer measurements remain GPU-lease work.

## 7. Horizontal scaling
Cells as the unit of scale ([ADR 0004](adr/0004-hyperscale-through-cells.md)). _TODO (WS3, WS5): cell sizing from capacity runs._

## 8. Failure scenarios and recovery
_TODO (WS7): failure matrix (pod, node, zone, region, control plane, Redis, Postgres), retries with budget, cancellation, backpressure, load shedding, rolling upgrades, model versions._

## 9. API specifications
### 9.1 Existing surfaces
OpenAI-compatible chat/completions, embeddings, audio, images, video handlers (`src/inference/core/handlers/`); Anthropic messages surface (`src/inference/core/surfaces/anthropic.py`). _TODO: endpoint table._
### 9.2 Unified multimodal interface
_TODO (WS6): async job API for video/batch; real-time audio over WebSocket._

## 10. Database and metadata
Gateway models: `src/api_gateway/db/models/` (users, orgs, roles, API keys, audit, inference logs, usage, policies, deployments, invitations, settings). No project entity yet (_TODO WS9: projects ADR_). Orchestration schema: `src/orchestration/db/schema.sql`, migrations in `src/infra/schema/migrations/`.

## 11. Caching architecture
| Cache | Where | What |
|---|---|---|
| Auth context | inference edge, in-memory TTL | Resolved key/user context (no per-request gateway hop) |
| Quota check | inference edge, in-memory TTL | Recent quota decisions |
| JWKS | gateway | External IdP keys |
| Prefix / KV | engine (vLLM APC), router index (llm-d) | Reused attention state |
| Model weights | `src/orchestration/models/model_cache/`, HF/OCI mirrors | Avoid repeated downloads; air-gap support |

## 12. Security boundaries
_TODO (WS9): trust boundaries (client ↔ edge, edge ↔ engine, control plane ↔ cluster), header stripping, least-privilege RBAC for the llm-d mode (`deploy/kind/manifests/llmd-namespace-bootstrap.yaml`), secrets handling, identity contract (`interfaces.md` §7)._

## 13. Bottlenecks and mitigations
_TODO (WS5): from measured platform limits and capacity runs._

## 14. Deployment architecture
_TODO (WS8): single node (Compose), Kubernetes (Helm), air-gapped, upgrades, backup/restore._

## 15. Testing and benchmarking strategy
- **Unit and integration tests** with a recorded baseline; CI-equivalent suite as the regression gate.
- **Middleware tested under the mounted unified app** (prefix-handling bugs in the Mistakes Log).
- **Real-software tests on kind** with `llm-d-inference-sim` (platform behaviour; engine timing simulated).
- **Simulator validation:** invariants, property-based KV tests, analytical checks, golden scenarios, calibration error.
- **GPU leases:** scripted, dry-run on CPU first; first lease A10 24GB (single-GPU calibration, KV saturation, prefix/offload), later H100-class for throughput and multi-GPU.
- Every result has a manifest (`interfaces.md` §1).

## 16. Simulator
_TODO (WS3): summary of the model; detailed docs live with `src/simulator/`._

## 17. Enterprise controls
Extension points with open-source defaults (`interfaces.md` §6). _TODO (WS9): projects, model access policies, quotas, cost reporting, audit coverage._

## 18. Readiness for the later restructure (brief Part B)
Today's design keeps the monorepo/open-core move cheap: plugin seams with open-source defaults, frameworks wrapped behind our interfaces, an identity contract both future providers can satisfy, contracts written as schemas, new code in self-contained folders ([ADR 0007](adr/0007-auth-diffusion-monorepo-decisions.md)).

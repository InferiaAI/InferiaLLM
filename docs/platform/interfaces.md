# Shared Interfaces (contract between sessions)

**Version:** v1.1 (2026-10-31). v0 was written with the plan; v1 was checked against the code and official docs by Session 1 (see the change log).
**Owner:** Session 1. Other sessions propose changes under "Requests" in their status file; Session 1 updates this doc and bumps the version; the user merges.
**Rule:** build against this document. If your code can't follow it, raise a request; don't silently diverge.

Items marked **[verify]** are believed correct but must be checked against official docs or code by the session that first depends on them. Record the result in the change log at the bottom.

---

## 1. Evidence labels and the results manifest

Every published number carries exactly one label:

| Label | Meaning |
|---|---|
| `MEASURED` | Observed on real software and/or real hardware. State which: if engine timing came from `llm-d-inference-sim`, say "MEASURED (platform), engine timing simulated" |
| `SIMULATED` | Output of `inferia-sim` |
| `EXTRAPOLATED` | Analytical scaling of MEASURED or SIMULATED results (for example, cell results × N cells) |

Every run writes `bench/results/<YYYY-MM-DD>-<run-id>/manifest.json`. The simulator also embeds the same object in its summary output.

```json
{
  "schema_version": "1",
  "run_id": "2026-11-02-h100-vllm-sweep-01",
  "label": "MEASURED",
  "label_detail": "real GPU",
  "created_at": "2026-11-02T10:15:00Z",
  "git_sha": "<commit>",
  "git_dirty": false,
  "command": "<exact command line used>",
  "tool": {"name": "vllm bench serve | guidellm | k6 | inferia-sim", "version": "<x.y.z>"},
  "config_hash": "<sha256 of the scenario/config file>",
  "seed": 1,
  "hardware": {"provider": "<neo-cloud name | local | simulated>", "gpu_model": "H100-SXM-80GB", "gpu_count": 1,
               "driver": "<nvidia driver>", "cuda": "<cuda>", "cpu": "<model>", "ram_gb": 0},
  "engine": {"name": "vllm", "version": "<x.y.z>", "image": "<image@digest>", "flags": ["--enable-prefix-caching"]},
  "model": {"id": "<hf id>", "revision": "<sha>", "dtype": "bf16", "quantization": null},
  "workload": {"description": "<text>", "dataset": "<name or generator>", "arrival": "<poisson rate=… | trace>",
               "input_tokens": "<distribution>", "output_tokens": "<distribution>"},
  "derived_from": [],
  "notes": ""
}
```

- `derived_from` lists run ids: the calibration runs for a SIMULATED result, or the source runs for an EXTRAPOLATED one.
- Charts carry their label in the title or as a watermark.

---

## 2. Metric names and units

These are canonical names for reports, simulator output and dashboards. Units are seconds, tokens and ratios (0–1). Percentiles are suffixed `_p50`, `_p95`, `_p99`.

| Canonical name | Unit | Meaning |
|---|---|---|
| `requests_per_second` | req/s | Completed requests per second |
| `output_tokens_per_second` | tok/s | Generated tokens per second (aggregate) |
| `ttft_seconds` | s | Time to first token (client-observed unless stated) |
| `itl_seconds` | s | Inter-token latency |
| `e2e_seconds` | s | End-to-end request latency |
| `queue_seconds` | s | Time waiting before first scheduling |
| `kv_cache_utilization` | ratio | Fraction of KV blocks in use |
| `prefix_cache_hit_ratio` | ratio | Prefix-cache hits / queries (token- or block-based; state which) |
| `preemptions_total` | count | Requests preempted |
| `shed_total`, `failed_total`, `cancelled_total` | count | Requests rejected by admission, failed, cancelled |
| `gpu_utilization` | ratio | Busy fraction (simulated: compute-or-memory-bound time / wall time) |
| `throughput_per_node_tokens_per_second` | tok/s | Output tokens per node |

**Existing InferiaLLM Prometheus metrics** (`src/inference/core/metrics.py`; keep them, add alongside):
- `inferia_inference_requests_total`
- `inferia_inference_duration_seconds`
- `inferia_inference_ttft_seconds`
- `inferia_upstream_in_flight_requests`
- `inferia_upstream_queued_requests`

New Prometheus metrics use the prefix `inferia_` and the canonical names above (for example `inferia_shed_total`, labelled `workload_class`).

**vLLM's names**, for scraping on GPU leases. Verified 2026-10-31 against docs.vllm.ai (latest) "Production Metrics"; re-check for the pinned image (`engines.yaml` pins `vllm/vllm-openai:v0.22.1`) when the lease runs:

| vLLM metric | Canonical |
|---|---|
| `vllm:kv_cache_usage_perc` (0–1) | `kv_cache_utilization` |
| `vllm:num_preemptions` | `preemptions_total` |
| `vllm:num_requests_running`, `vllm:num_requests_waiting` | running / queued |
| `vllm:time_to_first_token_seconds` | `ttft_seconds` (server-side) |
| `vllm:inter_token_latency_seconds` | `itl_seconds` |
| `vllm:request_time_per_output_token_seconds` (per-request TPOT histogram) | per-request TPOT (not the same as ITL) |
| `vllm:e2e_request_latency_seconds` | `e2e_seconds` (server-side) |
| `vllm:prefix_cache_hits`, `vllm:prefix_cache_queries` (both counted in **tokens**; scraped as `…_total` counters, e.g. `vllm:prefix_cache_hits_total`, as `llm-d-inference-sim` v1.0.0 exports them) | `prefix_cache_hit_ratio` (token-based) |

---

## 3. Workload classes

One enum, used by the API edge, the scheduler/admission logic, the simulator and the dashboard.

| Class | Typical requests | Priority (higher wins) | Preemptible | Default SLO focus |
|---|---|---|---|---|
| `realtime_audio` | Streaming STT/TTS sessions | 100 | No | Per-chunk latency |
| `interactive_chat` | Chat/completions, vision chat, streaming | 80 | No (can preempt lower classes) | TTFT p95, ITL p95 |
| `long_context` | Prompts above the long-context threshold | 60 | Swap rather than recompute | TTFT (relaxed), throughput |
| `embedding` | `/v1/embeddings`, rerank | 50 | n/a (short, no KV growth) | Throughput |
| `video_job` | Video generation/processing (async job API) | 30 | Checkpoint per stage | Job completion time |
| `batch` | Offline/batch API | 10 | Yes, resumable | Deadline (hours) |

- **Assignment:** the request type plus an explicit request option; tenants can't self-assign a higher class than their policy allows.
- **Thresholds and SLO numbers:** configuration, not constants. Defaults are proposed by Session 3 and recorded here in v1.
- **Python:** `class WorkloadClass(str, Enum)` with the values above. Location TBD by Session 3 (proposed `src/common/workload.py`, a shared hot spot, so a small isolated commit).
- **Don't confuse it with `workload_type`.** Orchestration already has a *deployment-level* `workload_type` (`"inference"` default, `"external"` for external endpoints; see `deployment_server.py` ~L694, ~L1718 and `controller.py::_extract_workload_type`). `WorkloadClass` is *per request*. Keep the names distinct and don't reuse or rename `workload_type`.

---

## 4. Request headers (edge → inference gateway → backend)

| Header | Set by | Meaning |
|---|---|---|
| `x-inferia-request-id` | Edge (generated if absent) | Correlation id across logs, traces and usage events |
| `x-inferia-workload-class` | Edge | A value from §3 |
| `x-inferia-priority` | Edge | Integer priority after policy (§3 default unless the policy overrides) |
| `x-inferia-session-id` | Edge (from the client or derived) | Session affinity / prefix locality |
| `traceparent` | Edge | W3C trace context (OpenTelemetry) |

**Mapping to the llm-d EPP** (verified 2026-10-31 against llm-d's "EPP HTTP Headers Reference"). **The header names changed between versions:**
- the latest docs (v0.9.0+) treat `x-llm-d-*` as canonical and accept `x-gateway-*` as deprecated aliases
- the 0.8 docs list them the other way round

Session 3 has pinned **llm-d v1.0.0** (router v1.0.0, GIE v1.6.2). **The request-header names are NOT yet verified for v1.0.0**: Session 3's spike exercised prefix routing only, which uses no headers. Session 3 will confirm them (EPP source at v1.0.0 plus a live test) in its T3; until then, treat the table below as documentation-based.

Verified facts for v1.0.0, from Session 3's spike:
- **`InferenceObjective` API:** use **`llm-d.ai/v1`**. The EPP logs that `llm-d.ai/v1alpha2` is deprecated, even though the v1.0.0 guides still show it.
- **CRDs:** the llm-d CRDs come from the llm-d router release manifests (`https://github.com/llm-d/llm-d-router/releases/download/v1.0.0/manifests.yaml`). GIE v1.6.2 `v1-manifests.yaml` contains only `inferencepools.inference.networking.k8s.io/v1`.
- **Routing ground truth for tests:** `llm-d-inference-sim` returns `x-inference-pod`, `x-inference-port` and `x-inference-namespace` response headers when `POD_NAME` / `POD_NAMESPACE` are set.

| InferiaLLM | Sent to the EPP as (latest naming) | Effect |
|---|---|---|
| `x-inferia-workload-class` (+ policy priority) | `x-llm-d-inference-objective: <InferenceObjective name>`, one `InferenceObjective` per class with the §3 priority | Priority admission and drop under saturation; no objective means priority 0 |
| `org_id` (tenant) | `x-llm-d-inference-fairness-id: <org id>` | Fair sharing across tenants (default flow `default-flow` if omitted) |
| Class SLO targets | `x-llm-d-slo-ttft-ms`, `x-llm-d-slo-tpot-ms` | SLO-aware scheduling, where enabled |
| `x-inferia-session-id` | none (the EPP scores prefix-cache overlap itself) | Used by the Envoy `RING_HASH` fallback path and by the simulator |

- **Response translation.** The EPP reports drops in `x-llm-d-request-dropped-reason` (`rejected-saturated`, `evicted`, `rejected-ttl-expired`, `rejected-no-endpoints`, …). The edge translates these into a 429/503 with `Retry-After` and counts them in `shed_total`, labelled by reason.
- **No spoofing.** Clients must never be able to set these headers: the edge strips any incoming `x-inferia-*`, `x-llm-d-*` and `x-gateway-*` before setting its own.

---

## 5. `DataPlaneBackend` (how orchestration deploys and reaches models)

**There are two deploy paths today**, and they matter for where llm-d plugs in (verified 2026-10-31):

| Path | Where | Used for |
|---|---|---|
| **Direct adapter (live path for Kubernetes and DePIN)** | `deployment_server._provisioning_route()` (~L334) returns `"direct_adapter"` for providers whose adapter has `CAPABILITIES.supports_direct_provisioning`, then `direct_provision.provision_direct_node()` → the `ProviderAdapter` from `ADAPTER_REGISTRY` (`provisioning/engine/registry.py`: `k8s` → `KubernetesAdapter` in `src/providers/k8s/k8s_adapter.py`, `nosana`, `akash`) | `k8s`, `nosana`, `akash` pools |
| Node-based strategies (legacy/worker path) | `ModelDeploymentWorker` (`model_deployment/worker.py`) → `RuntimeResolver.resolve()` → `runtime_strategies` (`vllm`, `localai`, `worker`; wired in `server.py` ~L627). `runtime_resolver.py` says "llmd strategy wiring is not complete in this service yet" | worker/on-prem nodes |

The other routes are `"self_register"` (inferia-worker pools) and `"reconciler"` (aws/gcp/azure via Pulumi).

**v1 decision: llm-d plugs in as a `ProviderAdapter` on the direct-adapter route. No new parallel interface.** "DataPlaneBackend" is the *concept*; in code it maps onto the existing contract (`provisioning/engine/base.py::ProviderAdapter`):

| Concept | Existing `ProviderAdapter` member |
|---|---|
| capabilities | `CAPABILITIES: ProviderCapabilities` (e.g. `supports_direct_provisioning`, `endpoint_http_probeable`, `readiness_timeout_seconds`) |
| deploy | `provision_node(provider_resource_id, pool_id, region, use_spot, metadata, provider_credential_name) -> dict` (its return includes `expose_url`) |
| status / endpoint | `wait_for_ready(provider_instance_id, timeout, ...) -> str` (the endpoint), plus node status polling used by `direct_provision` |
| delete | `deprovision_node(...)` (must be idempotent and verify the resources are gone) |
| logs | `get_logs`, `get_log_streaming_info` |
| (cluster mode) | `provision_cluster`, `deploy_service`, `stop_service`, `get_cluster_status` (optional, non-abstract) |

**Session 3's proposal (ADR 0008, Proposed): option 1, with raw llm-d resources rendered from the pinned chart.**
- **Pool metadata key:** `dataplane: "llm-d"`.
- **`direct_provision`** passes `pool_meta["dataplane"]` into the adapter metadata.
- **New hook:** `ProviderAdapter.capabilities_for(metadata) -> ProviderCapabilities`, defaulting to `get_capabilities()`, lets the llm-d mode enable `endpoint_http_probeable`.
- **`provider_instance_id` prefix:** `inferia-llmd-<hex>`.

**Two ways to add llm-d.** Session 3 chose option 1:
1. **A data-plane mode of `KubernetesAdapter`** selected by pool metadata (for example `metadata.dataplane = "llm-d"`). No new provider key, so no `compute_pools.provider` DB enum change. *Recommended.*
2. **A new adapter key in `ADAPTER_REGISTRY`** (for example `k8s-llmd`). Needs a check of the `compute_pools.provider` DB enum (`on_prem` is already an enum value aliasing `worker`) and probably a migration.

**Rules:**
- **READY means served.** READY / RUNNING only after an HTTP readiness probe succeeds (Mistakes Log 2026-06-17: "premature RUNNING"). For Kubernetes, `endpoint_http_probeable` is False today because the endpoint may be cluster-internal. If the gateway endpoint is reachable from orchestration, enable probing for the llm-d mode; otherwise probe from inside the cluster (an init/readiness gate) and document which.
- **Delete is safe to retry** and verifies the resources are gone.
- **No silent fallback** to a different backend.
- **Leave dead code alone until it's confirmed dead.** Don't extend `runtime_strategies` for llm-d. `src/orchestration/infra/k8s_llmd_client.py` (`LLMdK8sClient`, an `llmd.ai/v1` `llmdeployments` CRD) is referenced nowhere; delete it only after confirming that with graphify + grep, and record the evidence.

## 6. Extension points (plugin-ready, per `plan.md` §3 rule 2)

Each extension point is a Python `Protocol` with an open-source default implementation, resolved through a small registry. The registry and the first protocols are created by Session 4 in `src/common/extensions/`; Session 3 adds `AdmissionPolicy`. Selection is by configuration (`INFERIA_EXT_<KIND>=package.module:Class`); entry points come later with the open-core split.

| Kind | Protocol (async unless noted) | OSS default |
|---|---|---|
| `audit_sink` | `emit(event: AuditEvent) -> None` | Existing Postgres audit log: `AuditService.log_event` (`src/api_gateway/audit/service.py:24`) |
| `usage_sink` | `record(events: list[UsageEvent]) -> None` (batched) | Current path: `RequestLogger.log` → `log_inference` (`src/inference/client.py:169`) + `track_usage` (`:150`); then the async pipeline (§8) |
| `quota_policy` | `check(ctx: RequestContext, estimate: UsageEstimate) -> QuotaDecision` | Existing behaviour: `check_quota` (`src/inference/client.py:99`, cached), plus orchestration's `QuotaRepository` (`src/orchestration/repositories/quota_repo.py`) for compute quotas |
| `admission_policy` | `admit(ctx: RequestContext, cls: WorkloadClass, load: LoadSnapshot) -> AdmissionDecision` (ADMIT, QUEUE, or SHED with `retry_after_s`) | Bounded per-class queues + priority shedding |
| `token_verifier` | `verify(token: str) -> UserContext` | Existing resolution: `resolve_token_to_user_context` (`src/api_gateway/rbac/middleware.py:380`) dispatching to `_resolve_local_token` / `_resolve_external_token` / `_resolve_oidc_token` |

**Rules:**
- **Defaults keep current behaviour.** Adding an extension point must not change behaviour while the default is selected; prove it with the existing tests plus a test that swaps in a fake implementation.
- **Core never imports enterprise code.** Implementations depend on core, never the reverse.

---

## 7. Identity contract

The target that both future providers must satisfy: Better Auth (self-hosted) and inferia-auth (SaaS).

- **Format:** JWT, verified locally through JWKS with no per-request network call. Asymmetric EdDSA (Ed25519) is the target algorithm.
  - Today, external modes already use EdDSA via JWKS (`src/api_gateway/rbac/jwks_verifier.py`).
  - **Local mode currently issues HS256 tokens** (`jwt_algorithm` in `src/api_gateway/config.py`). Don't change local issuance in this phase.
- **The contract inside InferiaLLM is the resolved `UserContext`** (`src/api_gateway/schemas/auth.py:101`): `user_id`, `username`, `email`, `roles`, `permissions`, `org_id`, `quota_limit`, `quota_used`, `is_active`. Every auth mode must produce a complete one. Code downstream of authentication depends on `UserContext`, never on raw token claims.
- **Raw token claims differ by mode today:**
  - **Local:** `sub`, `username`, `email`, `exp`, `iat`, `type`, `roles` (role **names**), `org_id`, with **no `permissions` claim**; permissions are resolved from the DB (`src/api_gateway/rbac/auth.py::create_access_token`).
  - **inferiaauth:** catalog `permissions`, `roles` (role-instance UUIDs), `org_id`, `aud`, and `is_superuser` for break-glass only.
- **Target claims** for future providers (Better Auth self-hosted, inferia-auth SaaS): `iss`, `aud`, `sub`, `exp`, `iat`, `org_id`, `permissions` (list, catalog vocabulary), `roles` (opaque; never gate on role names in external modes), `is_superuser` (optional).
- **Permission vocabulary:** canonical keys use the catalog form `inferiallm:<resource>:<action>` (inferia-auth). Local code checks `PermissionEnum`; the bridge is `rbac/permissions.py::CATALOG_PERMISSION_MAP` + `expand_catalog_permissions()`. **Any new permission must be added to both** (Mistakes Log 2026-06-09; `test_catalog_permission_map.py`).
- **Org context:** every request resolves to exactly one active `org_id`. External-mode orgs get a local shadow org (`rbac/external_org.py`).

---

## 8. Usage event (for the async usage pipeline)

```json
{
  "schema_version": "1",
  "event_id": "<uuid v4; idempotency key>",
  "ts": "<ISO-8601 UTC, request completion time>",
  "request_id": "<x-inferia-request-id>",
  "org_id": "...", "user_id": "...", "api_key_id": "... | null",
  "deployment_id": "...", "model": "...", "request_type": "chat|completion|embedding|image|audio|video|...",
  "workload_class": "interactive_chat",
  "status_code": 200, "cancelled": false, "shed": false,
  "prompt_tokens": 0, "completion_tokens": 0, "input_count": null, "n_items": null,
  "ttft_ms": null, "latency_ms": 0, "is_streaming": true
}
```

- **Delivery is at-least-once;** consumers dedupe on `event_id`.
- **No prompts, completions or client IPs** in usage events. Payload and IP logging stay in `inference_logs` (`src/api_gateway/db/models/inference_log.py`), governed by the existing policy flags.
- **Fields line up with `inference_logs`** (`deployment_id`, `user_id`, `model`, `latency_ms`, `ttft_ms`, `prompt_tokens`, `completion_tokens`, `status_code`, `is_streaming`), so the batch writer can populate it. Add `applied_policies` (list) to the event when the writer needs it.
- The current per-request path (`RequestLogger.log` → gateway `/internal/logs/create` + `track_usage`) must keep working behind a fallback flag until the pipeline is proven.

---

## 9. Simulator config and output (summary)

**Config:** YAML validated by Pydantic. Top-level keys:
- `hardware`, `cluster`, `models`, `deployment`, `router`, `autoscaler`, `admission`, `workload`, `failures`, `run` (seed, duration, warmup, output dir)
- Field details live in `src/simulator/` (Session 2) and are documented in the simulator README.

**Summary output** (`summary.json`, consumed by the capacity report and the dashboard's Capacity Planner):

```json
{
  "manifest": { "...": "see §1, label SIMULATED" },
  "scenario": {"name": "64xH100-chat", "config_hash": "..."},
  "totals": {"requests": 0, "completed": 0, "failed": 0, "shed": 0, "cancelled": 0, "preempted": 0},
  "throughput": {"requests_per_second": 0, "output_tokens_per_second": 0},
  "latency": {"ttft_seconds": {"p50": 0, "p95": 0, "p99": 0}, "itl_seconds": {"p50": 0, "p95": 0, "p99": 0},
              "e2e_seconds": {"p50": 0, "p95": 0, "p99": 0}, "queue_seconds": {"p50": 0, "p95": 0, "p99": 0}},
  "kv": {"utilization_mean": 0, "utilization_p95": 0, "prefix_cache_hit_ratio": 0},
  "gpu": {"utilization_mean": 0},
  "per_node": [{"node": "n0", "gpu_model": "H100", "output_tokens_per_second": 0, "kv_utilization_mean": 0}],
  "slo": {"ttft_p95_target_s": 0, "met": true},
  "artifacts": {"requests": "requests.parquet", "timeline": "timeline.parquet", "plots": ["..."]}
}
```

---

## Change log
- **v0** (2026-10-31): initial contract, written with the plan.
- **v1** (2026-10-31, Session 1):
  - **§5 rewritten.** Kubernetes deploys use the direct-adapter route (`ProviderAdapter`), not `runtime_strategies`; llm-d plugs in as a `KubernetesAdapter` data-plane mode (recommended) or a new adapter key. v0 wrongly said to plug into `runtime_strategies`.
  - **§4:** llm-d EPP header mapping added (version-dependent names; `x-llm-d-*` canonical in latest docs), plus dropped-reason translation and a wider strip rule.
  - **§2:** vLLM metric names verified; per-request TPOT distinguished from ITL; prefix-cache counters are token-based.
  - **§3:** distinguished from the existing deployment-level `workload_type`.
  - **§6:** code locations for each OSS default.
  - **§7:** the contract is `UserContext`; actual per-mode claims documented (local tokens have no `permissions` claim).
  - **§8:** aligned with `inference_logs`; IPs excluded.
- **v1.1** (2026-10-31, Session 1, from Session 3's spike):
  - **§2:** `_total` counter names noted.
  - **§4:** llm-d v1.0.0 pinned; `InferenceObjective` is `llm-d.ai/v1`; CRD source noted; `x-inference-pod` test header added. Header names explicitly unverified until Session 3's T3.
  - **§5:** Session 3's proposed shape recorded (pool `dataplane` key, `capabilities_for` hook, id prefix; ADR 0008).

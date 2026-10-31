# 0008 — Data-plane install: raw llm-d (rendered manifests) over KServe `LLMInferenceService`

**Addendum to ADR 0002.** Drafted by Session 3 (status/session-3.md, 2026-10-31) and filed by Session 1. **Decision owner:** the user.

## Context
ADR 0002 picks Kubernetes + llm-d (Gateway API Inference Extension `InferencePool` + EPP) + vLLM. It leaves open whether orchestration manages one KServe `LLMInferenceService` custom resource or the llm-d resources directly. The plan's rule: choose KServe if it installs cleanly on kind within half a day.

## Evidence (checked 2026-10-31 against pinned releases)
1. **Raw llm-d works on kind, CPU only, first try.** About 2 minutes to a serving router, using two CRD manifests plus one Helm chart (`llm-d-router-standalone` v1.0.0), with no Gateway implementation, cert-manager or other operators. Precise prefix routing result (MEASURED (platform), engine timing simulated; 12 sessions × 6 turns, two seeds):

   | | stickiness | token prefix-hit ratio |
   |---|---|---|
   | EPP (precise prefix) | 1.000 | 0.809 (workload ceiling ≈ 0.81) |
   | kube-proxy random | 0.25–0.32 | 0.445–0.458 |

2. **KServe v0.21.0 (latest) pins an older llm-d stack:** GIE v1.5.0, llm-d router v0.10.0, Gateway API v1.5.1 (its quick-install script, lines 646–664). llm-d v1.0.0 uses GIE v1.6.2 and router v1.0.0. Its chart bundles its own older CRDs.
3. **KServe's installer deletes and re-applies the GIE/llm-d CRDs at older versions** (~lines 1043–1066). On a shared enterprise cluster already running llm-d, that would break existing workloads.
4. **Install surface:** KServe adds cert-manager, Envoy Gateway, Envoy AI Gateway, LeaderWorkerSet, MetalLB (on kind) and a webhook-bearing controller. That's much more to operate and to bundle for air-gapped installs.
5. **Feature lag:** v1.0.0 router features (prefix-cache-affinity filter, token-load scorer, replay-on-connect KV events, the `llm-d.ai/v1` `InferenceObjective`) reach KServe later. Cache-aware scheduling is the brief's critical item.
6. **What KServe offers:** a single custom resource, templates for prefill/decode disaggregation and wide-EP via LeaderWorkerSet, and autoscaler integration.
7. **Not verified empirically:** a KServe install on kind. Running KServe's installer script was blocked pending the user's permission. Points 2–5 come from KServe's own pinned files, so they hold regardless.

## Decision (Proposed)
**Raw llm-d.** Orchestration renders and applies the llm-d resources itself:
- the model-server Deployment
- the `InferencePool`
- the EPP and Envoy proxy, with its plugin ConfigMap, Service and RBAC

Versions are pinned (GIE v1.6.2, router v1.0.0). Resources are generated with `helm template` from the pinned chart by a checked-in script, vendored into the repo, and applied with the Kubernetes Python client (server-side apply). There's no runtime `helm` binary, no OCI access, no command-building risk, and it works air-gapped; a test pins the chart digest.

KServe stays a documented alternative, to revisit when it tracks llm-d ≥ v1.0 and we need its prefill/decode or wide-EP templates.

## Consequences
- **llm-d becomes a data-plane mode of `KubernetesAdapter`**, selected by pool metadata `dataplane: "llm-d"`. No new provider key and no DB enum change.
- **Two small, additive shared changes** in orchestration:
  - `direct_provision.provision_direct_node` passes `pool_meta["dataplane"]` into the adapter metadata (today pool metadata never reaches the adapter).
  - a `ProviderAdapter.capabilities_for(metadata)` hook, defaulting to `get_capabilities()`, so the llm-d mode can set `endpoint_http_probeable=True` and gate RUNNING on a probe *through the router*.
- **`provider_instance_id` prefix `inferia-llmd-<hex>`** routes `wait_for_ready`, `deprovision_node` and `get_logs` to the llm-d path. Delete is label-selected across every kind it created, then verified gone.
- **We maintain the rendered templates.** Upgrades mean re-render, diff and test.
- **Found along the way:** `KubernetesAdapter` honours `metadata["namespace"]` on provision but hardcodes `"default"` in `wait_for_ready`, `deprovision_node` and `get_logs`. Session 3 fixes this in T2.

## Status
Proposed (2026-10-31). Awaiting the user. A KServe trial on a throwaway cluster can be run if the user permits KServe's installer.

## Sources
- `status/session-3.md` (evidence, run manifests in Session 3's scratchpad: `runs/2026-10-31-kind-prefix-routing-01`, `-02`)
- KServe v0.21.0 `hack/setup/quick-install/llmisvc-dependency-install.sh`
- llm-d router v1.0.0 release manifests: https://github.com/llm-d/llm-d-router/releases/download/v1.0.0/manifests.yaml
- KServe LLMInferenceService docs: https://kserve.github.io/website/docs/model-serving/generative-inference/llmisvc/llmisvc-envoy-ai-gateway

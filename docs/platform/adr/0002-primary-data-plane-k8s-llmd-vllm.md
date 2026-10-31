# 0002 — Primary data plane: Kubernetes + llm-d + vLLM; Dynamo as alternative

## Context
Today, InferiaLLM routes inference through Envoy clusters built by its own xDS server with `lb_policy: ROUND_ROBIN` (`src/orchestration/api/xds.py` `_build_cluster`). That ignores load and prefix-cache locality, which drive TTFT and throughput at scale.

Kubernetes deploys go through the direct-adapter route to `KubernetesAdapter` (`src/providers/k8s/k8s_adapter.py`; see `interfaces.md` §5). An existing `LLMdK8sClient` (`src/orchestration/infra/k8s_llmd_client.py`) targets an `llmd.ai/v1` `llmdeployments` CRD that couldn't be found in llm-d's docs, and it's referenced nowhere.

## Decision
- **Primary data plane:** Kubernetes + **llm-d**, meaning the EPP inference scheduler on the Gateway API Inference Extension (InferencePool), behind an Envoy-based gateway, with **vLLM** engines.
- **Routing** uses llm-d's precise prefix-cache-aware scoring fed by vLLM KV events.
- **Integration:** a `ProviderAdapter` on the direct-adapter route; recommended as a data-plane mode of `KubernetesAdapter` selected by pool metadata.
- **NVIDIA Dynamo** is the documented alternative backend (KV router, Planner, KVBM, NIXL), and its AI Configurator is a cross-check for our simulator.
- **KServe `LLMInferenceService` vs raw llm-d manifests:** decided by Session 3's spike, recorded as an addendum.

## Alternatives considered
| Option | For | Against |
|---|---|---|
| **llm-d (chosen)** | Kubernetes-native standard APIs; precise prefix routing; KServe wraps it; `llm-d-inference-sim` allows CPU-only testing of real routing | Kubernetes required; several components to install |
| Dynamo as primary | Richest feature set (Planner, KVBM, NIXL, encoder disaggregation), plus a mocker | Heavier runtime, more NVIDIA-specific; harder to run on a laptop |
| KServe only | One custom resource | It wraps llm-d anyway; more install surface |
| Ray Serve | Good for multi-stage pipelines | A second orchestrator next to Kubernetes |
| Keep Envoy round-robin | Exists today | No load or cache awareness |

## Consequences
- **Laptop demos without GPUs:** kind + inference-sim give real routing behaviour, with engine timing labelled simulated (ADR 0005).
- **Fallback if the spike fails:** Envoy least-request + ring-hash through our xDS, keeping the 2026-06-20 `INNER JOIN` fix.
- **Version pinning matters:** llm-d EPP header names changed between versions (`x-gateway-*` ↔ `x-llm-d-*`; `interfaces.md` §4).

## Status
Accepted (2026-10-31). The KServe-vs-raw addendum is pending.

## Sources
- llm-d precise prefix-cache-aware routing: https://llm-d.ai/docs/guide/Installation/precise-prefix-cache-aware
- llm-d EPP HTTP headers: https://llm-d.ai/docs/api-reference/epp-http-headers
- KServe LLMInferenceService with the Inference Gateway: https://kserve.github.io/website/docs/model-serving/generative-inference/llmisvc/llmisvc-envoy-ai-gateway
- llm-d-inference-sim: https://github.com/llm-d/llm-d-inference-sim
- Dynamo profiler (AIC, mocker): https://docs.nvidia.com/dynamo/latest/components/profiler/profiler-guide.md

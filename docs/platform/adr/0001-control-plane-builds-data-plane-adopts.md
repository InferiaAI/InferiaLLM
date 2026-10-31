# 0001 — InferiaLLM is the control plane; the data plane is adopted

## Context
The brief asks for a platform "not another inference wrapper", built on mature frameworks (Dynamo, vLLM, SGLang, TensorRT-LLM, Ray, KServe, Kubernetes). InferiaLLM already has:
- a control plane: auth/RBAC, policy, audit, orchestration across providers (`src/api_gateway`, `src/orchestration`, `src/providers`)
- an OpenAI-compatible edge (`src/inference`)

It does not have competitive in-engine scheduling or KV management, and building those would duplicate vLLM/SGLang/Dynamo.

## Decision
**InferiaLLM builds:**
- the API surface (multimodal, OpenAI/Anthropic-compatible)
- tenancy, auth, RBAC, quotas, usage and audit
- workload classification, admission and priority policy
- multi-provider orchestration (Kubernetes, cloud, DePIN, self-hosted workers)
- the model registry, recipes and tuning presets
- the capacity simulator and planner, and the benchmark harness
- the dashboard and self-hosted packaging

**It adopts:**
- inference engines (vLLM primary; SGLang, TensorRT-LLM and vLLM-Omni through recipes)
- in-engine KV management (PagedAttention, automatic prefix caching)
- KV offload (vLLM's offloading connector or LMCache)
- inference-aware routing (llm-d EPP on the Gateway API Inference Extension)
- Kubernetes, Prometheus/Grafana/OpenTelemetry, Postgres and Redis

## Alternatives considered
- **Build our own engine-level scheduler/KV manager.** Rejected: years of work already done better upstream, and it fails the brief's "don't reinvent".
- **A thin proxy over hosted APIs.** Rejected: that is exactly the "inference wrapper" the brief rules out, and it can't be self-hosted.

## Consequences
- Our value lives in orchestration, admission, routing policy, capacity engineering, governance and operations.
- We take upstream release cadence and breaking changes as a risk. Mitigations: pin versions, wrap every external framework behind our own interfaces (`interfaces.md` §5, §6), and run a benchmark suite per upgrade.

## Status
Accepted (2026-10-31).

## Sources
- `docs/plans/2026-10-31-platform-scale/brief.md` (Part A objective)
- `plan.md` §3 D1

# kind: llm-d data plane on a laptop (CPU only)

A single-node kind cluster running the data plane InferiaLLM targets (plan D2):

```
client ─► Envoy sidecar ─ext_proc─► llm-d EPP (endpoint picker)
              │                         ▲  KV-cache events (ZMQ :5556, replay :5559)
              └──────────► sim pod ─────┘
```

- **Gateway API Inference Extension** `InferencePool` (v1.6.2): the set of model-server pods.
- **llm-d router** (`llm-d-router-standalone` chart, v1.0.0): Envoy plus the EPP, configured for
  *precise prefix-cache-aware routing*: the EPP subscribes to every pod's KV-cache events, keeps
  an index of which blocks each pod holds, and sends a request to the pod with the longest cached
  prefix unless that pod is too loaded.
- **llm-d-inference-sim** (v1.0.0) ×4: vLLM stand-ins that simulate a KV cache, publish real
  KV-cache events, and serve the vLLM HTTP API, including `/v1/*/render`.
- A **baseline** Service over the same pods (kube-proxy, random per connection, no EPP), used to
  compare cache-unaware balancing.

Engine timing comes from the simulator, so results are labelled **MEASURED (platform), engine timing simulated**.

## Prerequisites

| Tool | Version used | Source |
|---|---|---|
| Docker | 28.5 | — |
| kind | v0.33.0 | https://github.com/kubernetes-sigs/kind/releases/tag/v0.33.0 |
| kubectl | v1.37.2 | https://dl.k8s.io/release/v1.37.2/bin/linux/amd64/kubectl |
| helm | v3.19.0 (the version llm-d's `helpers/client-setup/install-deps.sh` pins) | https://get.helm.sh/helm-v3.19.0-linux-amd64.tar.gz |
| python3 | ≥ 3.10, standard library only (demo script) | — |

All three binaries can be installed user-level (`~/.local/bin`); verify each one against its published `.sha256`.

Resources: about **2.5 GB of free disk** while the cluster is up (node image plus router, Envoy and sim images), and roughly 1 CPU and 1 GiB RAM.

## Use

```bash
./deploy/kind/up.sh                                   # create or converge (idempotent)
python3 deploy/kind/demo/prefix_routing.py --out /tmp/run1
./deploy/kind/down.sh                                 # delete and verify nothing is left
```

| Entry point | URL (host, 127.0.0.1 only) | Path |
|---|---|---|
| llm-d router | `http://127.0.0.1:18911` | NodePort 30911 → Envoy sidecar :8081 → EPP picks the pod |
| baseline | `http://127.0.0.1:18912` | NodePort 30912 → kube-proxy → random pod |

Environment overrides: `CLUSTER` (default `inferia-s3`), `NAMESPACE` (`inferia-llmd`), `RELEASE` (`spike`), `MIN_FREE_GB` (`2`).
Every `kubectl`/`helm` call is pinned to `--context kind-$CLUSTER`, so other clusters are never touched.

## The routing demo

`demo/prefix_routing.py` sends the same workload through both entry points. The defaults are 12 sessions × 6 turns, each session with a unique ~1,500-word prefix, 4 sessions in flight, and a 0.5 s think time. Before each entry point it restarts the sims, so neither run inherits the other's cache. It reports:

- **stickiness**: the share of turns that land on the pod that served the session's previous turn. The ground truth is the sim's `x-inference-pod` response header.
- **token prefix-cache hit ratio**: the deltas of the sims' `vllm:prefix_cache_hits_total` / `vllm:prefix_cache_queries_total`.
- client latency p50/p95 (sim timing).

It writes `results.json` plus a `manifest.json` (`docs/platform/interfaces.md` §1).

Result on 2026-10-31 (two runs, seeds 1 and 2, each from a fresh cluster). Label: MEASURED (platform), engine timing simulated.

| Entry point | Stickiness | Pods per session | Token prefix-hit ratio | Latency p50 (sim timing) |
|---|---|---|---|---|
| llm-d EPP (precise prefix) | 1.000 / 1.000 | 1.00 / 1.00 | 0.809 / 0.809 | 28 / 32 ms |
| baseline (kube-proxy random) | 0.250 / 0.317 | 3.25 / 3.17 | 0.445 / 0.458 | 316 / 314 ms |

The ceiling for this workload is about 5/6 × 0.97 ≈ 0.81: each session's first turn always misses, and later turns reuse only the full 64-token blocks of the prefix. llm-d reaches it. It also spread the 12 sessions exactly 3 per pod, so the stickiness is cache affinity, not every request piling onto one pod. Latency differs only because the sim charges 200 µs per *uncached* prompt token; that is a simulated cost, not GPU performance.

## Versions and sources (pinned)

| Component | Pin | Official source |
|---|---|---|
| kind node | `kindest/node:v1.37.0@sha256:a1ed56cf…580ae5` (kind v0.33.0 default) | kind v0.33.0 release notes |
| Gateway API Inference Extension CRDs | v1.6.2 `v1-manifests.yaml`, sha256 `f410dfca…c4ad0d` (`inferencepools.inference.networking.k8s.io/v1`) | https://github.com/kubernetes-sigs/gateway-api-inference-extension/releases/tag/v1.6.2 |
| llm-d CRDs | llm-d-router v1.0.0 `manifests.yaml`, sha256 `bae0ae78…d2ba21e3` (`InferenceObjective`, `InferenceModelRewrite`, `llm-d.ai`) | https://github.com/llm-d/llm-d-router/releases/tag/v1.0.0 |
| llm-d router chart | `oci://ghcr.io/llm-d/charts/llm-d-router-standalone` v1.0.0, digest `sha256:c1d1af55…a59e975024c` | llm-d v1.0.0 `guides/env.sh` |
| EPP image | `ghcr.io/llm-d/llm-d-router-endpoint-picker:v1.0.0@sha256:024188e9…7df2c` | same |
| Envoy sidecar | `docker.io/envoyproxy/envoy:distroless-v1.33.2` (set by the chart) | chart `values.yaml` |
| Simulator | `ghcr.io/llm-d/llm-d-inference-sim:v1.0.0@sha256:1908c421…b93ec8` | https://github.com/llm-d/llm-d-inference-sim/releases/tag/v1.0.0 |

The router configuration (`values/router-precise-prefix.values.yaml`) is llm-d v1.0.0's
[`precise-prefix-cache-routing`](https://github.com/llm-d/llm-d/tree/v1.0.0/guides/precise-prefix-cache-routing) guide,
adapted for the simulator. The differences are commented at the top of the file.

Simulator flags were checked against its v1.0.0 [configuration](https://github.com/llm-d/llm-d-inference-sim/blob/v1.0.0/docs/configuration.md)
and [KV-cache](https://github.com/llm-d/llm-d-inference-sim/blob/v1.0.0/docs/kv-cache.md) docs.

## Gotchas found while building this

- **Boolean sim flags take `--flag` or `--flag=true`.** v1.0.0 *refuses* `--flag true`, the form the sim repo's own `manifests/deployment_kvcache.yaml` still uses.
- **The sim's KV-cache doc says a vLLM render sidecar is required.** It isn't needed for routing between sims. The EPP tokenizes through the sims' own `/v1/completions/render`, and `BlockStored` events carry `token_ids`, so both sides see the same simulated tokens. The sidecar matters only when sims must match a real HuggingFace tokenizer (the `vllm-openai-cpu` image is 1.2 GB compressed).
- **The guide's base values request 4 CPU / 8 GiB** for the EPP and again for the Envoy sidecar. Here they are overridden down.
- **Keep the EPP at one replica** with this routing config. The token-load-scorer's in-flight accounting and the KV index are per process (the guide's own note).
- **`InferenceObjective` `llm-d.ai/v1alpha2` is deprecated in v1.0.0** (the EPP logs "use llm-d.ai/v1"). New objectives (per-class priority, plan WS6/WS7) should use `llm-d.ai/v1`.

## Driving it from InferiaLLM (the llm-d data-plane mode)

`up.sh` also applies `manifests/llmd-namespace-bootstrap.yaml`: namespace `inferia-llmd`, the shared EPP ServiceAccount and its RBAC, plus a least-privilege Role for InferiaLLM's orchestration. Do this once per namespace, as an admin. After that, a **k8s pool whose metadata has `"dataplane": "llm-d"`** serves every deployment through llm-d (code: `src/providers/k8s/llmd/`):

```bash
# pool (REST, via the gateway)
POST /api/v1/deployment/createpool  {"provider":"k8s", ..., "metadata":{"dataplane":"llm-d"}}
# deployment on it (only llm-d-inference-sim is supported for now; vLLM needs verified /v1/*/render)
POST /api/v1/deployment/deploy      {"pool_id":"<id>","engine":"llm-d-inference-sim","inference_model":"sim-model",
                                     "gpu_per_replica":0,"configuration":{"replicas":2}, ...}
```

Per deployment it creates `inferia-llmd-<hex>`: the model-server Deployment, an `InferencePool`, the router (Envoy + EPP), and their Services and ConfigMaps. The deployment turns RUNNING only after a request is served **through the router**. Terminating it deletes everything and verifies it is gone.

| Setting (control-plane env) | Default | Purpose |
|---|---|---|
| `K8S_LLMD_NAMESPACE` | `inferia-llmd` | Namespace for llm-d deployments (must have the bootstrap applied) |
| `K8S_SERVICE_TYPE` | `ClusterIP` | Existing adapter setting; `NodePort` lets an out-of-cluster control plane reach the router |
| `UPSTREAM_ALLOWED_INTERNAL_HOSTS` | empty | Existing inference SSRF allowlist (exact hosts). A NodePort endpoint is a private node IP (kind: `172.18.0.x`), so an out-of-cluster control plane must list it; an in-cluster one uses Service DNS names and needs nothing |

`K8S_LLMD_NAMESPACE` is not in `.env.example`, like the adapter's other `K8S_*` settings: `test_env_parity` requires `.env` and `.env.example` to have identical keys.

Verified live on 2026-10-31 with InferiaLLM run from source against this cluster: create → RUNNING (`probe=ready`) → 6 chat turns through `/inf`, all served by the pod holding the cached prefix (token hit ratio 0.808) → terminate → delete → no objects or pods left.

Running InferiaLLM from a venv: `inferiallm init` calls a bare `python3` for `bootstrap_db.py`, so put the venv's `bin/` first on `PATH` (or activate the venv). Otherwise it picks up the system interpreter.

## What this does not cover yet

- **The Gateway-mode chart** (`llm-d-router-gateway`, behind a Gateway API implementation). That mode is needed for HTTPRoute weights (canary or model versions); standalone mode is enough for routing.
- **An Envoy `ROUND_ROBIN` baseline.** The baseline here is kube-proxy random, which balances per connection.
- **InferiaLLM itself.** Driving these resources from InferiaLLM's orchestration is session-3 T2.

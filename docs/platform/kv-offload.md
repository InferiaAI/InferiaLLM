# Tiered KV offload deployment contract

This contract prepares a direct Kubernetes vLLM deployment for **engine-owned**
KV cache tiers. Kubernetes supplies GPU/CPU resources and persistent storage;
it does not transfer KV blocks or implement cache coherence. The engine owns
those operations.

## Accepted configuration

Only the direct `engine: vllm` path currently accepts `kv_cache`. InferiaLLM
must own the vLLM command line—`cmd` or `command` alongside this configuration
is rejected so a deployment cannot silently omit the required flags.

```yaml
engine: vllm
gpu_allocated: 1
ram_gb_allocated: 18       # CPU tier plus 2 GiB process headroom
kv_cache:
  hbm_utilization: 0.90    # optional 0 < value <= 1
  cpu_offload:
    gib: 16
    backend: native
```

The renderer appends the documented vLLM flags:

```text
--gpu-memory-utilization 0.9
--kv-offloading-size 16
--kv-offloading-backend native
```

`ram_gb_allocated` must be at least `cpu_offload.gib + 2`; that value becomes
both the Pod memory request and limit. The CPU buffer is a vLLM process-memory
reservation, not a Kubernetes volume. vLLM documents that the offload size is
total across tensor-parallel ranks and that setting it activates CPU offload
with the selected backend. See the [vLLM engine arguments](https://docs.vllm.ai/en/v0.21.0/configuration/engine_args/#--kv-offloading-size)
and [KV offloading guide](https://docs.vllm.ai/en/latest/features/kv_offloading_usage/).

## Explicit gates

- The llm-d path currently uses `llm-d-inference-sim`, so it refuses
  `kv_cache`; it must never claim simulator support as real vLLM offload.
- NVMe secondary tiers are refused. They need a verified connector version,
  storage-volume and lifecycle contract for the pinned vLLM image.
- `backend: lmcache` is refused. It needs a separately pinned LMCache
  server/sidecar and connection contract; no implicit localhost service is
  assumed.
- Real offload behavior, HBM pressure, transfer latency and recovery remain
  **GPU-pending**. Render tests only prove the deployment contract, not that
  blocks were offloaded or restored.

## Validation scope

Unit tests render a fake Kubernetes adapter and assert the exact flags and Pod
memory resources. GPU lease validation must run the matching vLLM image, check
startup logs and KV metrics, and record results as **MEASURED** only then.

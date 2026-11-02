# inferia-sim

A discrete-event simulator of LLM inference serving: requests → replicas →
continuous batching → paged KV cache → latency, throughput and memory
results. Every output is labelled **SIMULATED**
(`docs/platform/interfaces.md` §1). Simulated numbers are never GPU
measurements, and until calibration (T5) the performance model uses
**uncalibrated** efficiency placeholders.

Status: the CPU-verifiable scheduler, workload, routing, queue-depth
autoscaling, failure/retry and CPU/NVMe offload slices are implemented. The
remaining simulator work is P/D disaggregation, additional autoscaler
signals/scale-in, and Parquet/plot artifacts.

## Run it

```bash
inferia-sim list-scenarios
inferia-sim validate 8xH100                      # bundled name or a path
inferia-sim run 8xH100 --seed 1 --out bench/results/2026-10-31-8xH100-seed1
inferia-sim run my.yaml --out out/ --write-requests --check-invariants
```

`run` writes `summary.json` (interfaces §9, with the §1 manifest embedded)
and `manifest.json`, plus `requests.csv` with `--write-requests`. To make a
rerun byte-identical, set `SOURCE_DATE_EPOCH` (it fixes `created_at`). Any
other run with the same scenario and seed gives the same numbers.

The core needs only `pydantic` and `PyYAML` (both base dependencies of
InferiaLLM).

## Scenario file

YAML validated by `simulator/config.py`. Unknown keys and unimplemented
options are rejected. Top-level keys:

| Key | What it holds |
|---|---|
| `name`, `description` | Identification |
| `hardware.perf` | Roofline efficiencies: `mfu` (0.5), `hbm_efficiency` (0.8), `step_overhead_s` (0.003), `collective_latency_s` (1e-5), `activation_reserve_gb` per GPU (2.0), `calibration` label. **Defaults are uncalibrated placeholders.** |
| `cluster.pools[]` | `name`, `gpu` (catalog name), `gpus_per_node`, `nodes` |
| `models[]` | Catalog model names used by deployments |
| `deployment[]` | `name`, `model`, `pool`, `tensor_parallel`, `replicas`, `max_num_seqs`, `max_num_batched_tokens`, `max_model_len`, `block_size` (16), `gpu_memory_utilization` (0.92), `enable_chunked_prefill`, `enable_prefix_caching`, `allocator` (`paged` \| `contiguous`), `contiguous_reserve` (`max_model_len` \| `request`), `preemption` (`recompute` \| `swap`), `swap_space_gb` per GPU, or `offload: {tier: cpu|nvme, capacity_gb, bandwidth_gbps}` with swap preemption |
| `router.policy` | `round_robin`, `random`, `least_outstanding`, `kv_aware`, `session_affinity`, or `class_to_pool` |
| `autoscaler` | Queue-depth scale-out with a min replica count, warm pool and cold/warm start delays. `kv_utilization` and `ttft_p95` are reserved for a later slice. |
| `failures` | Timed replica, node, zone or fraction outages; optional recovery; retries only before first token, bounded by attempts and retry budget. |
| `admission` | `max_waiting_per_replica` (shed beyond it), `retry_after_s` |
| `workload.arrival` | `process: poisson`, `rate_rps` |
| `workload.classes[]` | `class` (interfaces §3), `weight`, `deployment` (required when there are several), `input_tokens` / `output_tokens` (`{dist: fixed, value}` or `{dist: lognormal, median, sigma, min, max}`), optional `shared_prefix: {groups, tokens}` placed before the input tokens |
| `run` | `seed`, `duration_s` (arrival window), `warmup_s`, `drain`, `max_drain_s`, `slo_ttft_p95_s` |

Validation also checks that each TP group fits in a node and the pool can
place every replica, that `max_num_batched_tokens >= max_num_seqs` and (without
chunked prefill) `>= max_model_len` (both as vLLM enforces), and that each
replica's KV cache holds at least `max_model_len` tokens.

Bundled scenarios (`scenarios/`): `tiny`, `8xH100`, `64xH100`, `256xH100`,
`hetero-h100-a100-l40s`. They are configurable scenarios, not benchmark claims.

## Model

**Catalog** (`catalog/`): GPU datasheet values and Hugging Face model configs,
each with a source URL and retrieval date. Dense TFLOPS are half of NVIDIA's
"with sparsity" figures where only those are listed. Parameter counts and
KV bytes/token are derived from the architecture (Llama-3.1-8B BF16:
131,072 B/token; 70B: 327,680).

**KV capacity per replica** = (HBM × `gpu_memory_utilization` − activation
reserve) × TP − weights, in blocks of `block_size` tokens.

**KV manager** (`kv.py`): paged blocks with ref counts and an LRU free queue;
automatic prefix caching over full blocks with chained keys (vLLM's design);
eviction of zero-ref cached blocks from the queue head; reverse-order free;
last prompt token always recomputed; swap to a legacy CPU pool or an explicit
CPU/NVMe tier. Explicit tiers cap blocks by `capacity_gb` and charge PCIe plus
tier bandwidth for each swap; a `contiguous`
allocator for comparison (per-request max-length reservation, first fit,
external fragmentation reported).

**Scheduler** (`scheduler.py`), one per replica, modelled on vLLM V1:
decodes, then running prefills, then (if nothing was preempted this step)
swapped-in and waiting requests in priority order (class priority, then
arrival). Chunked prefill under a token budget. On a failed allocation it
preempts the lowest-priority, latest-arriving running request (recompute,
or swap with recompute fallback). Admission rejects prompt + output >
`max_model_len` and sheds when the waiting queue is full.

**Step time** (`perf.py`): roofline, max(compute, memory) + TP all-reduce +
fixed overhead; formulas in the module docstring.

**Engine** (`engine.py`): `heapq` events ordered by (time, kind, sequence),
one per forward pass plus arrivals, scaling and outage/recovery events;
requests arriving at the same instant join the same step. A hard outage
cleans the failed replica's KV state. Only requests that have not emitted a
token can retry, so the model never silently duplicates streamed output.

## Outputs

`summary.json` follows interfaces §9 and adds: `totals.unfinished`,
`failure_reasons`, `window`, `kv.{evictions, swapped_out_blocks,
internal_waste_ratio_mean, external_fragmentation_mean, prefix_cache_basis}`,
`gpu.{achieved_flops_fraction_mean, achieved_hbm_bandwidth_fraction_mean}`,
`admission`, `littles_law` and `calibration`.

- Latency percentiles cover requests that **arrive** in [warmup, duration];
  throughput counts completions and tokens emitted in that window.
- ITL percentiles come from a log histogram (within 0.5% of exact).
- `prefix_cache_hit_ratio` is token-based, like vLLM's counters.
- `gpu.utilization_mean` = compute-or-memory-bound time / wall time
  (interfaces §2), averaged over GPUs.

## Validation in the test suite

`src/tests/simulator/`: request conservation; KV block conservation and
per-step item checks at every step; Little's law (exact over a drained
horizon, within 5% at steady state); byte-identical reruns; hypothesis
state machines over the block managers; hand-computed roofline and KV
values; and behaviour tests for saturation, swap, priority, prefix caching,
contiguous allocation, chunked/non-chunked prefill and admission.

## Known limitations (this slice)

- Uncalibrated: MFU, η and overheads are placeholders until GPU-lease data (T5).
- No cross-node tensor or pipeline parallelism; dense models only (no MoE).
- Embedding lookup counted as a matmul; no CUDA-graph or kernel effects; no sampling cost.
- vLLM V1 preempts only by recompute; `swap` models V0-style swapping.
- Block keys are 64-bit hashes (collision probability ~1e-4 at 6e7 distinct blocks).
- Not yet: P/D disaggregation, KV-utilization/TTFT autoscaling and scale-in,
  Parquet/plots (T3).
- Speed: ~600 simulated requests/s of wall time on the 8xH100 scenario; the
  1M-requests-in-5-minutes budget (T8) needs profiling work.

# Session 2 status

## T3 foundation: routers and workload (2026-11-02)

Implemented and verified in the current slice:

- router policies: random, least-outstanding, session affinity, KV-aware, and
  class-to-deployment mapping;
- multi-turn session request generation, preserving prior turns' prompt and
  output tokens for prefix-cache reuse;
- population-derived arrival rate, basic NHPP/MMPP/Hawkes synthetic arrival
  modes, and licensed JSONL trace replay;
- trace provenance in the simulation manifest.

Verification:

```text
pytest -q src/tests/simulator/test_t3.py -k 'session_affinity or kv_aware or least_outstanding or random_and_class_to_pool or multi_turn_sessions or trace_replay'
6 passed, 15 deselected
```

Remaining T3 work is intentionally not claimed complete: eviction/offload,
autoscaling, failure/retry handling,
prefill/decode disaggregation, and plot output. `test_t3.py` remains an
uncommitted acceptance-test scaffold for those follow-up slices. Before the
KV slice its full run reported 13 passed and 8 failed.

## T3 KV cache: eviction and offload (2026-11-02)

Implemented `lru` and `prefix_depth` eviction for cached-free paged KV
blocks. A configured CPU/NVMe offload tier now retains evicted prefix keys,
restores them on a later prefix hit, charges the configured bandwidth, and
reports restored-block counts.

```text
pytest -q src/tests/simulator/test_t3.py -k 'prefix_depth_eviction or offload_tier or engine_offload'
3 passed, 18 deselected
```

Still excluded from this slice: autoscaling, failure/retry handling,
prefill/decode disaggregation, and plot output.

The full scaffold after this slice reports **16 passed, 5 failed**; those
five failures correspond only to the explicitly excluded areas.

## T3 autoscaling (2026-11-02)

Autoscaling now models pre-validated dormant capacity activating after a
control-loop interval. Warm-pool capacity uses `warm_start_s`; additional
cold capacity uses `cold_start_s`. Results expose simulated scale-up counts
and the configured metric/target. This is a scheduling model, not a GPU
measurement or calibration result.

```text
pytest -q src/tests/simulator/test_t3.py -k autoscaler
1 passed, 20 deselected
```

## T3 output artifacts (2026-11-02)

`write_outputs(..., write_plots=True)` now writes dependency-free TTFT and
end-to-end SVG plots, each visibly watermarked `SIMULATED`.

```text
pytest -q src/tests/simulator/test_t3.py -k write_outputs
1 passed, 20 deselected
```

## T3 prefill/decode transfer timing (2026-11-02)

The disaggregation model now retains prefill TTFT and adds one explicitly
simulated KV transfer (latency plus configured bandwidth) before decoding.

```text
pytest -q src/tests/simulator/test_t3.py -k disaggregation
1 passed, 20 deselected
```

## T3 failures and retries (2026-11-02)

Replica/node/zone failure events now remove impacted work. Requests that have
emitted a token fail as mid-stream loss; waiting work retries subject to the
configured retry budget, attempt cap, and backoff. These are simulated
resilience outcomes, not production reliability measurements.

```text
pytest -q src/tests/simulator/test_t3.py -k 'failure_retries or retry_budget'
2 passed, 19 deselected
```

## CPU-backed capacity matrix (2026-11-03)

`scripts/run_simulator_capacity_matrix.py` produces repeatable per-scenario
summaries, manifests, request CSVs, and watermarked SVG latency artifacts.
The matrix is explicitly SIMULATED/uncalibrated and does not initiate GPU
calibration. A `tiny`, seed-1 matrix execution completed with invariant
checks and produced all expected artifacts.

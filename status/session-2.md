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
uncommitted acceptance-test scaffold for those follow-up slices. At this
checkpoint its full run reports 13 passed and 8 failed; the failures are the
remaining work named above.

# Architecture Decision Records

One file per decision: `NNNN-short-title.md`.

**Sections:** Context · Decision · Alternatives considered · Consequences · Status · Sources.

**Status values:**
- `Proposed`: awaiting the user or their manager
- `Accepted`
- `Superseded by NNNN`

Session 1 files ADRs; other sessions draft them in their status files.

| # | Title | Status |
|---|---|---|
| 0001 | InferiaLLM is the control plane; the data plane is adopted | Accepted |
| 0002 | Primary data plane: Kubernetes + llm-d + vLLM; Dynamo as alternative | Accepted (KServe vs raw llm-d pending Session 3's spike) |
| 0003 | Build our own discrete-event simulator, calibrated on real GPUs | Accepted |
| 0004 | Reach hyperscale estimates through cells, not brute force | Accepted |
| 0005 | Three evidence tiers: MEASURED, SIMULATED, EXTRAPOLATED | Accepted |
| 0006 | Multimodal through workload classes and per-class pools | Accepted |
| 0007 | Auth, diffusion and the monorepo restructure (2026-10-31 decisions) | Accepted (some sub-points Proposed) |

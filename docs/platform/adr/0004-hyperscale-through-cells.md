# 0004 — Reach hyperscale estimates through cells, not brute force

## Context
Simulating 100M users request by request for a full day isn't tractable or meaningful. Real hyperscale systems are partitioned into independent cells (region slices).

## Decision
Use a three-level method:
1. A **user model** turns registered users, activity ratios, requests per user and the peak factor into peak arrival rates analytically.
2. A **discrete-event simulation** runs at **cell** scale (8–256 GPUs; millions of requests over a peak window).
3. **Total = cells × per-cell capacity**, plus the global load-balancer and gateway tier sized from measured platform overhead.

Linearity is validated by simulating 1, 2, 4 and 8 cells and checking per-cell metrics stay flat. Results at level 3 are labelled EXTRAPOLATED (ADR 0005).

## Alternatives considered
- **Brute-force full-population simulation:** infeasible, and it adds no fidelity beyond the cell boundary.
- **Pure analytical scaling:** hides queueing and saturation dynamics.

## Consequences
- **The cell design is part of the production architecture**, not only a simulation trick: blast-radius isolation and independent scaling.
- **Cross-cell effects** (global routing imbalance, shared-tier contention) are modelled explicitly or stated as limitations.

## Status
Accepted (2026-10-31).

## Sources
- `plan.md` §3 D4, WS5 (activity-assumption table)

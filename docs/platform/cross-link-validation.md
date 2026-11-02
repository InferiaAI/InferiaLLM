# Cross-link validation — 2026-11-02

## Local platform-document links

The internal Markdown destinations among `spec.md`, `interfaces.md`, ADRs,
diagrams, capacity, operations, traceability, audit, demo and presentation
material were checked from this checkout. All resolve locally.

## Branch-owned implementation links

The platform index intentionally links to implementation artifacts that are
committed in, but not yet merged from, these worktrees:

| Link target family | Verified committed source |
|---|---|
| `src/simulator/`, `bench/` | `s2/simulator` and integrated commits on `s3/dataplane` |
| `deploy/kind/`, llm-d adapter and usage stream | `s3/dataplane` |
| `deploy/helm/`, extensions | `s4/enterprise-ui` and merged evidence on `s3/dataplane` |

These links are expected to resolve in the integrated repository. They are not
counted as broken documentation links in the current documentation-only
worktree. GPU execution artifacts are deliberately absent pending a lease.

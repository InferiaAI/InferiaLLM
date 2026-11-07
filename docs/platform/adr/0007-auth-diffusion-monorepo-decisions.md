# 0007 — Auth, diffusion and the monorepo restructure (2026-10-31 decisions)

## Context
The Inferia ecosystem spans several repos:
- **InferiaLLM:** public, Apache-2.0.
- **inferia-worker:** public, Go.
- **inferia-auth:** private, Go. Embedded OpenFGA ReBAC; SaaS-only, never deployed by customers.
- **InferiaDiffusion:** private, Python diffusers. In-memory video jobs, no tests.
- **InferiaAllSpark and InferiaGate:** private.

The manager's mandate (`brief.md` Part B) asks for one monorepo, an open-source core with closed-source enterprise plugins, and thoughtful use of Better Auth, Dynamo, llm-d and others.

## Decision
1. **inferia-auth stays as the SaaS (Inferia Cloud) identity service.**
2. **InferiaDiffusion stays an engine.** Before it scales it needs durable job storage, results in object storage instead of base64 in status responses, and tests.
3. **The main task proceeds inside InferiaLLM now.** The monorepo, open-core split and Better Auth (for self-hosted login) are a **separate, later restructure**, done at a quiet point after a milestone merges.
4. **Until then, current work follows the "monorepo-ready" rules** (`plan.md` §3):
   - new code goes in self-contained folders
   - would-be enterprise features sit behind extension points with open-source defaults (`interfaces.md` §6)
   - external frameworks are wrapped behind our own interfaces
   - shared definitions are written as schemas
   - no cross-repo changes or large renames
5. **Existing auth stays for now.** Both future providers must satisfy one identity contract: they produce a complete `UserContext`, and the target tokens are EdDSA via JWKS with catalog-vocabulary permissions (`interfaces.md` §7).

## Alternatives considered
- **Better Auth everywhere, replacing inferia-auth.** One stack, but it loses fine-grained ReBAC and forces InferiaGate to migrate. Deferred.
- **Hybrid: Better Auth for login + OpenFGA for authorisation.** The best long-term option on paper, but the most upfront work. Revisit at the restructure.
- **Replace InferiaDiffusion with vLLM-Omni.** Less code to own, but model coverage for today's models (SDXL/Flux/Z-Image/Kokoro) is unverified.
- **Monorepo first.** Cheaper for cross-repo work, but it delays the main task. Mitigated by the monorepo-ready rules and by deferring cross-repo changes.

## Proposed (awaiting the manager)
- Which features become closed-source enterprise plugins.
- Whether inferia-auth and InferiaDiffusion become open source inside the monorepo.
- The repo strategy: a public monorepo plus a private enterprise repo (recommended, because it forces clean plugin boundaries), or a private monorepo with a public export (the Dagster/Copybara model).
- The open-source licence (Apache-2.0 today).
- InferiaGate's scope.
- Whether the product keeps the "InferiaLLM" name.

## Consequences
- **Two identity providers will coexist** (Better Auth self-hosted, inferia-auth SaaS). Drift is controlled by the identity contract and a single permission catalog (`rbac/permissions.py::CATALOG_PERMISSION_MAP`; Mistakes Log 2026-06-09).
- **The engine recipe still lives in three places** (Python Kubernetes, the Nosana builder, the Go worker) until the restructure introduces shared contracts.

## Status
Accepted (2026-10-31); the "Proposed" items await the manager.

## Sources
- `~/projects/inferiaAuth/CLAUDE.md` ("not deployed by anyone except us")
- `~/projects/InferiaDiffusion/README.md`
- `~/projects/inferia-worker/internal/runtime/recipes/recipes.go` (ported from the Nosana job builder)
- Dagster hub-and-spoke model: https://dagster.io/blog/monorepos-the-hub-and-spoke-model-and-copybara
- Better Auth SSO / SCIM / JWT plugins: https://www.better-auth.com/docs/plugins/sso · https://www.better-auth.com/docs/plugins/scim · https://www.better-auth.com/docs/plugins/jwt

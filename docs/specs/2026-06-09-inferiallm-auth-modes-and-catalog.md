# InferiaLLM — Three Auth Modes + Catalog Self-Declaration (Design Spec)

**Date:** 2026-06-09
**Status:** Approved for planning
**Owners:** platform

## Goal

Make InferiaLLM's authentication mirror **InferiaGate** exactly: three deployment
modes, and have InferiaLLM **self-declare its own permission/role catalog** to
InferiaAuth instead of InferiaAuth hardcoding it.

## Background — the three ways InferiaLLM ships

| Mode (`AUTH_PROVIDER`) | InferiaGate equiv | Identity authority | Local org/user/team |
|---|---|---|---|
| `local` (open-source) | `primitive` | InferiaLLM's own user/password (basic auth) | **active** |
| `oidc` (enterprise self-hosted) | `oidc` | the enterprise's **own** OIDC IdP | hidden (IdP owns it) |
| `inferiaauth` (SaaS) | `inferiaauth` | **InferiaAuth** (`auth.inferia.ai`) | hidden (InferiaAuth owns it) |

A user hits InferiaLLM, is redirected to the IdP's hosted login (InferiaAuth in
SaaS), signs in, and is redirected back with a session. In `oidc`/`inferiaauth`
modes, **Organization / User / Team management is owned by the IdP**, not by
InferiaLLM.

## Current state (what already exists)

InferiaLLM already has a substantial InferiaAuth integration under a single
`AUTH_PROVIDER=external` value (built per `docs/specs|plans/2026-05-23-inferia-auth-sso-integration.md`):

- **Redirect-SSO**: `rbac/oauth_router.py` (`/auth/start` PKCE → `inferia-auth/oauth/authorize`; `/auth/callback` → exchange code → hand token to dashboard via `/#access_token=`).
- **Token verification**: `rbac/jwks_verifier.py` (verify InferiaAuth EdDSA JWTs via cached JWKS).
- **Request auth**: `rbac/middleware.py` (`AUTH_PROVIDER=external` → try local JWT, fall back to JWKS-verify → shadow user → **roles/permissions straight from the token claims**).
- **Shadow users**: `rbac/shadow_user.py` (provision a local FK-anchor user on first external login).
- **Dashboard**: `apps/dashboard/src/services/authService.ts`, `hooks/useTokenFragmentConsumer.ts`, `pages/Login.tsx`.
- **Topology**: `deploy/docker-compose.sso.yml` (`make docker-up-sso`) — InferiaLLM + InferiaAuth + Caddy.
- **Permissions are HARDCODED in InferiaAuth**: `inferia-auth/cmd/server/seed.go` `inferiallmPermissions` map (the "v1 source of truth") + the `inferiallm-dashboard` OAuth client.

So `local` and (one) `external` mode exist; the gaps are: (1) `external` conflates
the two external modes, (2) the catalog is hardcoded in InferiaAuth, (3) local
org/user/team is not hidden in external modes.

## Target architecture

### 1. Three explicit modes (`AUTH_PROVIDER: local | oidc | inferiaauth`)

`config.py`: change `auth_provider: Literal["local","external"]` →
`Literal["local","oidc","inferiaauth"]`. Backward-compat: accept `external` as a
deprecated alias mapping to `inferiaauth` (log a deprecation warning) so the
existing `docker-compose.sso.yml` keeps working until updated.

Validation (`config.py` model validator):
- `inferiaauth` requires `EXTERNAL_AUTH_URL`, `EXTERNAL_AUTH_ISSUER`, `OAUTH_CLIENT_ID`, `OAUTH_REDIRECT_URI` (today's "external" rule).
- `oidc` requires the same OIDC discovery inputs (issuer, client id, redirect) but **no** InferiaAuth-specific catalog/gRPC.
- `local` requires none.

`rbac/middleware.py` — branch the request-auth path on mode:
- `local`: `_resolve_local_token` only (today's behavior).
- `inferiaauth`: try local (superadmin recovery) → `_resolve_external_token` (JWKS verify; **permissions/roles from the `permissions`/`roles` claims** — InferiaAuth-issued, catalog-backed).
- `oidc`: try local (superadmin) → `_resolve_oidc_token` (JWKS verify against the enterprise IdP; **no catalog claim** — derive role from a configurable group/role claim mapping; default "authenticated ⇒ admin of the shadow org", matching InferiaGate's documented `oidc` posture, with `OIDC_GROUPS_CLAIM` → role map as a follow-up).

`rbac/oauth_router.py`/`config.py`: `_require_external_mode()` → `_require_redirect_sso_mode()` (true for `oidc` **or** `inferiaauth`); the `/auth/start` + `/auth/callback` flow is identical for both (standard OIDC code+PKCE).

### 2. Catalog self-declaration (InferiaLLM owns its permission/role catalog)

**InferiaLLM defines the catalog** (new module `rbac/catalog.py`) — the contract
of every `inferiallm:<resource>:<action>` permission + the roles that bundle
them, mirroring InferiaGate's `internal/identity/catalog/catalog.go`:

- Permissions (the v1 set, taken from InferiaAuth's current hardcoded map):
  `inferiallm:deployment:{read,write,delete}`, `inferiallm:provider:{read,write}`,
  `inferiallm:user:{read,write}`, `inferiallm:org:{read,write}`,
  `inferiallm:audit:read`, `inferiallm:apikey:{read,write}`,
  `inferiallm:model:{read,write}`.
- Roles: `admin` (all), `operator` (writes for deployment/provider/model + reads), `viewer` (all `:read`). Each role validated to reference only declared permission keys.
- Each permission has `key`, `display_name`, `description` (the InferiaAuth `PUT /api/v1/services/:id/catalog` body shape — see `inferia-auth/internal/transport/rest/handler/catalog.go`).

**InferiaLLM declares it at boot** (new `rbac/catalog_declare.py`, called from
`app.py` startup when `auth_provider == "inferiaauth"`): `PUT
{EXTERNAL_AUTH_URL}/api/v1/services/inferiallm/catalog` with a short-lived
catalog-admin token (same mechanism InferiaGate uses — `CATALOG_ADMIN_TOKEN` /
the catalog declare flow). Idempotent; performs shrink reconciliation on the
InferiaAuth side. A `catalog declare failed` boot warning is non-fatal (the
catalog persists in InferiaAuth across restarts).

**InferiaAuth stops hardcoding** (`inferia-auth/cmd/server/seed.go`): remove the
`inferiallmPermissions` map + its FGA seed. Keep seeding the `inferiallm-dashboard`
OAuth **client** row (client registration is still InferiaAuth's job, like
`svc_inferiagate`). The FGA permission tree for `inferiallm` is now created by
the catalog declare call (the same `SeedApp`/`SeedTemplates` path the catalog
handler already uses for InferiaGate).

> The local `PermissionEnum` (`member:list`, `role:update`, …) governs **local
> mode** RBAC and stays. The declared `inferiallm:*` catalog is what InferiaAuth
> issues in the `permissions` claim and what the middleware enforces in
> `inferiaauth` mode. A small mapping (`rbac/permissions.py`) bridges the two
> where a route is gated in both modes.

### 3. Hide local org/user/team in external modes (mirror InferiaGate SaaS)

When `auth_provider in {oidc, inferiaauth}`, the IdP owns identity, so:
- **Backend**: gate the local management routes — `management/organizations.py`,
  `management/users.py`, `rbac/users_router.py`, and the role-management routes —
  to return `409 Conflict` / a redirect-to-IdP message (a `require_local_identity`
  dependency that 409s in external modes). The `auth_service.create_access_token`
  password-login path (`/auth/login`, `/auth/register`) is also gated off in
  external modes (superadmin recovery login stays).
- **Dashboard**: hide the Settings → Organization / Users & Teams / Roles
  surfaces when `VITE_AUTH_PROVIDER` ∈ {oidc, inferiaauth}; mirror InferiaGate's
  `hideInTiers`/runtime-mode detection. Shadow users + org-scoping FK anchors stay.
- **Local mode**: everything stays fully active (open-source self-serve).

## Cross-repo change summary

**InferiaLLM** (`package/src/inferia/services/api_gateway/`):
- `config.py` — 3-mode enum + per-mode validation + `external` back-compat alias.
- `rbac/catalog.py` (new) — the permission/role catalog.
- `rbac/catalog_declare.py` (new) — boot-time `PUT …/services/inferiallm/catalog`.
- `app.py` — call catalog-declare on startup (inferiaauth mode); branch middleware/router wiring per mode.
- `rbac/middleware.py` — 3-way branch; add `_resolve_oidc_token`.
- `rbac/oauth_router.py` — `_require_redirect_sso_mode` (oidc|inferiaauth).
- `management/*.py`, `rbac/users_router.py` — `require_local_identity` gate.
- `apps/dashboard/src/**` — hide identity surfaces in external modes.

**InferiaAuth** (`inferia-auth/`):
- `cmd/server/seed.go` — remove the hardcoded `inferiallmPermissions` FGA seed; keep the `inferiallm-dashboard` OAuth client seed.

## Security posture
- Fail-closed: unknown mode → boot error; external token verify failure → 401.
- Catalog-admin token for declare is short-lived (~900s), same as InferiaGate.
- Superadmin local recovery (`SUPERADMIN_EMAIL/PASSWORD`) works in all modes.
- No secrets in git; dev values in `docker-compose.sso.yml` are DEV-ONLY.

## Out of scope / deferred
- Fine-grained `OIDC_GROUPS_CLAIM` → role mapping for the generic `oidc` mode (Phase: start with "authenticated ⇒ admin of shadow org", same interim as InferiaGate's oidc mode).
- Per-org data-plane scoping changes beyond what shadow users already provide.
- Migrating existing local users to InferiaAuth (data migration is a separate effort).

## References
- InferiaGate catalog: `../InferiaGate/internal/identity/catalog/catalog.go` + `declare.go`.
- InferiaAuth catalog API: `../inferiaauth/internal/transport/rest/handler/catalog.go`.
- Prior SSO work: `docs/specs|plans/2026-05-23-inferia-auth-sso-integration.md`.
- InferiaGate three-mode model: `../InferiaGate/CLAUDE.md`.

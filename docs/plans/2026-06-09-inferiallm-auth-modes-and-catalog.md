# InferiaLLM Three Auth Modes + Catalog Self-Declaration — Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development or superpowers:executing-plans to implement task-by-task. Steps use `- [ ]` checkboxes.

**Goal:** Make InferiaLLM auth mirror InferiaGate — three modes (`local | oidc | inferiaauth`) — and have InferiaLLM self-declare its permission/role catalog to InferiaAuth instead of InferiaAuth hardcoding it.

**Architecture:** Generalize the existing `AUTH_PROVIDER=external` InferiaAuth integration into three explicit modes; add an InferiaLLM-owned catalog declared to InferiaAuth at boot; hide local org/user/team management in the two external modes; keep everything for `local` (open-source). Cross-repo: InferiaLLM (Python/FastAPI) + InferiaAuth (Go).

**Tech Stack:** Python 3.10–3.12, FastAPI, SQLAlchemy/asyncpg, pytest+pytest-asyncio, httpx, python-jose; React 19 + Vite + TanStack Query (dashboard); Go (InferiaAuth).

**Spec:** `docs/specs/2026-06-09-inferiallm-auth-modes-and-catalog.md`

**Commit convention:** Conventional commits. **Never** include Claude / Co-Authored-By footers.

---

## Phase 0 — Verify the existing SaaS SSO (gate before refactor)

Confirm the already-built `external` integration round-trips before changing it.

### Task 0.1: Bring up + smoke-test the SSO topology
**Files:** none (ops).
- [ ] **Step 1:** Add hosts entries: `127.0.0.1 inferia.local` and `127.0.0.1 auth.inferia.local` (`/etc/hosts`).
- [ ] **Step 2:** `make docker-up-sso` (builds inferia-auth + inferia-app images; brings up postgres/redis/caddy). Expected: all 4 services healthy (`make docker-logs-sso`).
- [ ] **Step 3:** Run the existing smoke script `scripts/sso_smoke.sh`. Expected: exits 0 (full redirect → login → callback → authenticated `/me` round-trip).
- [ ] **Step 4:** Manually: open `https://inferia.local`, confirm redirect to `https://auth.inferia.local`, log in (`admin@example.com` / `change-me-immediately-1234`), confirm redirect back + dashboard loads authenticated.
- [ ] **Step 5:** Record the baseline (what works) in the plan's "Phase 0 results" note. **No commit** (ops-only). If the smoke fails, fix the existing scaffold first and re-run before Phase 1.

---

## Phase 1 — Catalog self-declaration

InferiaLLM owns its catalog; InferiaAuth stops hardcoding it.

### Task 1.1: Define InferiaLLM's permission/role catalog
**Files:**
- Create: `package/src/inferia/services/api_gateway/rbac/catalog.py`
- Test: `package/src/inferia/services/api_gateway/rbac/tests/test_catalog.py`

- [ ] **Step 1: Write the failing test** (`test_catalog.py`):
```python
from inferia.services.api_gateway.rbac.catalog import CATALOG, to_declare_request

def test_every_role_permission_is_declared():
    keys = {p.key for p in CATALOG.permissions}
    for role in CATALOG.roles:
        for k in role.permissions:
            assert k in keys, f"role {role.name} references undeclared {k}"

def test_admin_has_all_permissions():
    admin = next(r for r in CATALOG.roles if r.name == "admin")
    assert set(admin.permissions) == {p.key for p in CATALOG.permissions}

def test_to_declare_request_shape():
    body = to_declare_request(CATALOG)
    assert set(body) == {"roles", "permissions"}
    assert all({"key", "display_name", "description"} <= set(p) for p in body["permissions"])
    assert all({"name", "permissions"} <= set(r) for r in body["roles"])
    # keys are colon-form inferiallm:<resource>:<action>
    assert all(p["key"].startswith("inferiallm:") for p in body["permissions"])
```
- [ ] **Step 2: Run, expect FAIL** — `pytest package/src/inferia/services/api_gateway/rbac/tests/test_catalog.py -v` → ImportError.
- [ ] **Step 3: Implement `catalog.py`** — `@dataclass Permission(key, display_name, description)`, `@dataclass Role(name, permissions: list[str])`, `@dataclass Catalog(permissions, roles)`. Define the v1 permission list (deployment/provider/user/org/audit/apikey/model × read/write/delete as in spec), roles `admin`/`operator`/`viewer`, and `to_declare_request(catalog) -> dict` projecting to the InferiaAuth `PUT /services/:id/catalog` body. Add a module-load assertion that every role permission is declared (raise at import if not).
- [ ] **Step 4: Run, expect PASS.**
- [ ] **Step 5: Commit** — `feat(rbac): define InferiaLLM permission/role catalog`.

### Task 1.2: Catalog declare client (boot-time PUT to InferiaAuth)
**Files:**
- Create: `package/src/inferia/services/api_gateway/rbac/catalog_declare.py`
- Test: `.../rbac/tests/test_catalog_declare.py`

- [ ] **Step 1: Write failing test** — using `httpx.MockTransport`, assert `declare_catalog(base_url, token)` issues `PUT {base}/api/v1/services/inferiallm/catalog` with the `to_declare_request(CATALOG)` body + `Authorization: Bearer <token>`, returns True on 200, returns False (no raise) on 5xx/network error.
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement** `async def declare_catalog(base_url, admin_token, *, client=None) -> bool` — httpx PUT, 30s timeout, length-guard inputs, swallow errors → return False + log warning (non-fatal, mirrors InferiaGate).
- [ ] **Step 4: Run, expect PASS.**
- [ ] **Step 5: Commit** — `feat(rbac): catalog declare client for InferiaAuth`.

### Task 1.3: Call declare on boot (inferiaauth mode only)
**Files:**
- Modify: `package/src/inferia/services/api_gateway/app.py` (startup)
- Modify: `config.py` (add `catalog_admin_token` setting, alias `CATALOG_ADMIN_TOKEN`)
- Test: `.../tests/test_app_startup_catalog.py`

- [ ] **Step 1: Write failing test** — with `auth_provider="inferiaauth"` + a mocked `declare_catalog`, assert app startup awaits `declare_catalog(external_auth_url, catalog_admin_token)`; with `auth_provider="local"`, assert it is NOT called.
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement** — in the FastAPI startup handler, `if settings.auth_provider == "inferiaauth" and settings.catalog_admin_token: await declare_catalog(settings.external_auth_url, settings.catalog_admin_token)`. Log success/failure; never raise.
- [ ] **Step 4: Run, expect PASS.**
- [ ] **Step 5: Commit** — `feat(app): declare catalog to InferiaAuth on boot (inferiaauth mode)`.

### Task 1.4: Remove the hardcoded inferiallm seed from InferiaAuth
**Files (InferiaAuth repo `../inferiaauth`):**
- Modify: `cmd/server/seed.go` (delete `inferiallmPermissions` + its FGA seed call; keep the `inferiallm-dashboard` OAuth client seed)
- Test: `cmd/server/seed_test.go` (or the relevant seed test)

- [ ] **Step 1:** Read `cmd/server/seed.go`; identify (a) the `inferiallmPermissions` map + the `SeedApp`/`SeedTemplates` call that seeds the inferiallm FGA tree, and (b) the `inferiallm-dashboard` OAuth client insert. Confirm the catalog handler (`internal/transport/rest/handler/catalog.go`) seeds FGA via the same `Seeder` path on declare.
- [ ] **Step 2: Update the seed test** — assert boot seed still inserts the `inferiallm-dashboard` client, and assert it NO LONGER seeds the inferiallm permission tree (that now arrives via the catalog API). Run, expect FAIL.
- [ ] **Step 3: Implement** — delete `inferiallmPermissions` + the FGA seed for inferiallm; keep the client seed. Build: `go build ./...`.
- [ ] **Step 4: Run** the auth test suite for the touched packages — expect PASS. Verify a fresh boot + an InferiaLLM catalog-declare produces the same FGA tuples as before (integration check against the SSO topology).
- [ ] **Step 5: Commit (InferiaAuth)** — `refactor(seed): let InferiaLLM self-declare its catalog; drop hardcoded perms`.

---

## Phase 2 — Three explicit modes (`local | oidc | inferiaauth`)

### Task 2.1: Config — 3-mode enum + per-mode validation + back-compat
**Files:**
- Modify: `config.py:160` (`auth_provider` Literal) + the model validator (`config.py:304`)
- Test: `.../tests/test_config_modes.py`

- [ ] **Step 1: Write failing tests** — (a) `AUTH_PROVIDER=inferiaauth` without the 4 external fields → validation error; (b) `AUTH_PROVIDER=oidc` requires issuer+client+redirect; (c) `AUTH_PROVIDER=external` (legacy) loads as `inferiaauth` with a deprecation warning; (d) `AUTH_PROVIDER=local` needs nothing.
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement** — `Literal["local","oidc","inferiaauth"]`; a `field_validator`/`model_validator` that normalizes `external`→`inferiaauth` (warn), and enforces required fields per mode. Add a `is_external_mode` helper property (`mode in {oidc, inferiaauth}`).
- [ ] **Step 4: Run, expect PASS.**
- [ ] **Step 5: Commit** — `feat(config): three auth modes (local|oidc|inferiaauth) with back-compat`.

### Task 2.2: Middleware — branch per mode
**Files:**
- Modify: `rbac/middleware.py` (the `use_external` branch + add `_resolve_oidc_token`)
- Test: `.../rbac/tests/test_middleware_modes.py`

- [ ] **Step 1: Write failing tests** — with a stubbed JWKS verifier: (a) `inferiaauth` mode → `_resolve_external_token` reads `permissions`/`roles` from claims; (b) `oidc` mode → `_resolve_oidc_token` builds UserContext with the interim "authenticated ⇒ admin" role + shadow org, no catalog claim required; (c) `local` mode → external paths never invoked.
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement** — replace `use_external = settings.auth_provider == "external" …` with a 3-way branch keyed on `settings.auth_provider`; add `_resolve_oidc_token(db, token)` (JWKS verify against the enterprise issuer → shadow user → role from `OIDC_GROUPS_CLAIM` map if set, else `["admin"]` interim). Reuse `_get_verifier()` (parameterized by issuer/audience already).
- [ ] **Step 4: Run, expect PASS.**
- [ ] **Step 5: Commit** — `feat(rbac): per-mode request auth (local|oidc|inferiaauth)`.

### Task 2.3: Redirect-SSO router covers both external modes
**Files:**
- Modify: `rbac/oauth_router.py` (`_require_external_mode` → `_require_redirect_sso_mode`)
- Test: `.../rbac/tests/test_oauth_router_modes.py`

- [ ] **Step 1: Write failing test** — `/auth/start` returns 302 in both `oidc` and `inferiaauth` modes; returns 503 in `local` mode.
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement** — gate on `settings.is_external_mode` (oidc|inferiaauth) instead of the old `external` string.
- [ ] **Step 4: Run, expect PASS.**
- [ ] **Step 5: Commit** — `feat(rbac): redirect-SSO available in oidc and inferiaauth modes`.

### Task 2.4: Update the SSO compose to the new mode value
**Files:**
- Modify: `deploy/docker-compose.sso.yml` (`AUTH_PROVIDER: external` → `inferiaauth`; `VITE_AUTH_PROVIDER: external` → `inferiaauth`; add `CATALOG_ADMIN_TOKEN`)
- [ ] **Step 1:** Set `AUTH_PROVIDER: inferiaauth`, `VITE_AUTH_PROVIDER: inferiaauth`, and a dev `CATALOG_ADMIN_TOKEN`.
- [ ] **Step 2:** `make docker-up-sso` + `scripts/sso_smoke.sh` → expect 0 (regression check that the rename + catalog-declare didn't break the round-trip).
- [ ] **Step 3: Commit** — `chore(sso): use inferiaauth mode + catalog-admin token in SSO compose`.

---

## Phase 3 — Hide local org/user/team in external modes

### Task 3.1: Backend gate — `require_local_identity`
**Files:**
- Create: `package/src/inferia/services/api_gateway/rbac/local_identity_guard.py`
- Modify: `management/organizations.py`, `management/users.py`, `rbac/users_router.py`, the role-management routes, and the local password-login routes in `rbac/oauth_router.py`/auth router
- Test: `.../tests/test_local_identity_guard.py`

- [ ] **Step 1: Write failing tests** — in `inferiaauth`/`oidc` mode, `GET/POST/PUT/DELETE` on `/organizations`, `/users`, `/roles`, `/auth/login`, `/auth/register` → `409` with a "managed by your identity provider" body; in `local` mode → normal behavior.
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement** — `def require_local_identity():` FastAPI dependency raising `HTTPException(409, …)` when `settings.is_external_mode`; add it to each local-identity router (`dependencies=[Depends(require_local_identity)]`). Keep the superadmin recovery login path exempt.
- [ ] **Step 4: Run, expect PASS.**
- [ ] **Step 5: Commit** — `feat(rbac): gate local org/user/team/login in external modes`.

### Task 3.2: Dashboard — hide identity surfaces in external modes
**Files:**
- Modify: `apps/dashboard/src/**` — the nav + Settings (Organization / Users & Teams / Roles) using `VITE_AUTH_PROVIDER`
- Test: the dashboard test(s) for nav/settings visibility

- [ ] **Step 1: Write failing test** — when `VITE_AUTH_PROVIDER` ∈ {oidc, inferiaauth}, the Settings → Organization/Users/Teams/Roles entries are not rendered (and routes redirect to a "managed by your IdP" notice); when `local`, they render.
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement** — a `useAuthMode()`/env read + a `hideIdentityInExternal` filter on the nav, plus a route guard notice (mirror InferiaGate's `hideInTiers` + the ConfigView per-org redirect).
- [ ] **Step 4: Run, expect PASS** (`cd apps/dashboard && npm test`).
- [ ] **Step 5: Commit** — `feat(dashboard): hide org/user/team/roles in external auth modes`.

---

## Phase 4 — Generic enterprise OIDC mode polish

### Task 4.1: `OIDC_GROUPS_CLAIM` → role mapping (optional, behind config)
**Files:**
- Modify: `config.py` (`oidc_groups_claim`, `oidc_role_map`), `rbac/middleware.py` `_resolve_oidc_token`
- Test: `.../rbac/tests/test_oidc_role_mapping.py`

- [ ] **Step 1: Write failing test** — a token with `groups: ["llm-admins"]` + `OIDC_ROLE_MAP={"llm-admins":"admin"}` → role `admin`; no matching group → `viewer` (configurable default).
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement** the mapping (default behavior unchanged when unset: interim `["admin"]`).
- [ ] **Step 4: Run, expect PASS.**
- [ ] **Step 5: Commit** — `feat(rbac): optional OIDC group→role mapping for enterprise mode`.

---

## Phase 5 — End-to-end verification (all three modes)

### Task 5.1: Mode matrix verification
- [ ] **Step 1: `local`** — `AUTH_PROVIDER=local`: register/login locally, org/user/team management works, no redirect to any IdP.
- [ ] **Step 2: `inferiaauth`** — `make docker-up-sso`: redirect to InferiaAuth → login → back; catalog visible in InferiaAuth (`GET /api/v1/services/inferiallm/catalog`); permissions claim enforced; local org/user/team hidden + 409.
- [ ] **Step 3: `oidc`** — point `EXTERNAL_AUTH_*` at a generic OIDC IdP (or InferiaAuth acting as plain OIDC); authenticated ⇒ admin of shadow org; local org/user/team hidden; no catalog declare.
- [ ] **Step 4:** Run the full suite — `make test` (Python) + `cd apps/dashboard && npm test` + InferiaAuth `go test ./...` (touched pkgs). Expect green.
- [ ] **Step 5: Update docs** — `docs/operations/auth.md` (the three modes), `CLAUDE.md` (mode table + catalog self-declaration). Commit.

---

## Self-review checklist (run before execution)
- [ ] Spec coverage: each spec section maps to a task above (3 modes ✓ T2.x, catalog ✓ T1.x, hide identity ✓ T3.x, oidc ✓ T4.1, verify ✓ T0/T5).
- [ ] No placeholders: every code/test step shows concrete content or a precise file+behavior.
- [ ] Type consistency: `CATALOG`, `to_declare_request`, `declare_catalog`, `require_local_identity`, `is_external_mode`, `_resolve_oidc_token` used consistently across tasks.
- [ ] Cross-repo: InferiaAuth change (T1.4) is the only Go task; everything else is InferiaLLM Python/React.

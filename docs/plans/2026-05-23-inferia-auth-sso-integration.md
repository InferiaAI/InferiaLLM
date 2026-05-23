# inferia-auth SSO Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make inferia-auth the SSO provider for InferiaLLM via OAuth2 Authorization Code + PKCE, with FGA-derived `roles`/`permissions` in JWT claims, a new branded React login UI, and a single `.env` toggle (`AUTH_PROVIDER=external`) that flips the system over.

**Architecture:** inferia-auth becomes an OIDC-lite IdP serving `/oauth/{authorize,token,userinfo,revoke}` + JWKS. A new React app `inferia-auth-ui` provides the branded login surface, mounted at the same origin as inferia-auth. InferiaLLM gateway verifies tokens locally via cached JWKS, reads `roles`/`permissions` claims to build `UserContext`. The dashboard redirects to inferia-auth for login, hits InferiaLLM's `/auth/callback` to exchange code for tokens.

**Tech Stack:** Go + Fiber v3 (inferia-auth), embedded OpenFGA, PostgreSQL, Ed25519 JWT, golang-migrate. Vite + React 19 + TypeScript + Tailwind + Shadcn (inferia-auth-ui, dashboard). Python 3.10-3.12 + FastAPI + SQLAlchemy + asyncpg + python-jose + httpx (InferiaLLM). Docker + Caddy for compose topology.

**Reference docs:**
- Spec: `docs/specs/2026-05-23-inferia-auth-sso-integration.md`
- Brand: `docs/specs/inferia-brand-spec.md`

**Repo locations:**
- InferiaLLM: `/storage/intern/hooman/work/InferiaLLM/` (current branch `feat/aws-ec2-provisioning`)
- inferia-auth: `/storage/intern/hooman/work/inferia-auth/`

**Commit convention:** Signed with `git -c user.signingkey=/home/ankit/.ssh/id_ed25519_gh -c gpg.format=ssh commit -S`. **Never** include Claude / Co-Authored-By footers.

---

## File Map (Phase-by-Phase)

### Phase A — inferia-auth backend

| Path | Action | Purpose |
|---|---|---|
| `migrations/000010_oauth_clients.up.sql` | create | `oauth_clients` table |
| `migrations/000010_oauth_clients.down.sql` | create | drop |
| `migrations/000011_oauth_codes.up.sql` | create | `oauth_codes` table |
| `migrations/000011_oauth_codes.down.sql` | create | drop |
| `migrations/000012_oauth_refresh_tokens.up.sql` | create | rotation chain |
| `migrations/000012_oauth_refresh_tokens.down.sql` | create | drop |
| `migrations/000013_oauth_sessions.up.sql` | create | SSO session cookies |
| `migrations/000013_oauth_sessions.down.sql` | create | drop |
| `openfga/model.fga` | modify | add `app`, `permission_set`, `permission` types |
| `internal/domain/oauth/code.go` | create | `Code`, `CodeChallenge` types |
| `internal/domain/oauth/client.go` | create | `Client`, `ClientType` |
| `internal/domain/oauth/refresh.go` | create | `RefreshToken` |
| `internal/domain/oauth/session.go` | create | `SsoSession` |
| `internal/infrastructure/repository/oauth_clients_postgres.go` | create | CRUD |
| `internal/infrastructure/repository/oauth_codes_postgres.go` | create | CRUD + single-use enforcement |
| `internal/infrastructure/repository/oauth_refresh_postgres.go` | create | CRUD + chain detection |
| `internal/infrastructure/repository/oauth_sessions_postgres.go` | create | CRUD |
| `internal/usecase/oauth/pkce.go` | create | `VerifyChallenge(verifier, challenge) error` |
| `internal/usecase/oauth/scopes.go` | create | `ExpandPermissions(userID, appNamespace) → []string` via FGA |
| `internal/usecase/oauth/authorize.go` | create | issues codes |
| `internal/usecase/oauth/token_exchange.go` | create | code/refresh → JWT |
| `internal/usecase/oauth/seed_apps.go` | create | idempotent app + permission_set seeder |
| `internal/transport/rest/handler/oauth_authorize.go` | create | `GET /oauth/authorize` |
| `internal/transport/rest/handler/oauth_token.go` | create | `POST /oauth/token` |
| `internal/transport/rest/handler/oauth_userinfo.go` | create | `GET /oauth/userinfo` |
| `internal/transport/rest/handler/oauth_revoke.go` | create | `POST /oauth/revoke` |
| `internal/transport/rest/handler/oidc_discovery.go` | create | `GET /.well-known/openid-configuration` |
| `internal/transport/rest/router.go` | modify | register new routes |
| `internal/infrastructure/token/jwt.go` | modify | extend `Issue()` with `aud`, `roles`, `permissions` claims |
| `cmd/server/main.go` | modify | seed client + apps on boot |

### Phase B — inferia-auth-ui

| Path | Action | Purpose |
|---|---|---|
| `inferia-auth-ui/package.json` | create | npm config |
| `inferia-auth-ui/vite.config.ts` | create | build → `dist/` |
| `inferia-auth-ui/tailwind.config.ts` | create | brand preset |
| `inferia-auth-ui/index.html` | create | SPA shell |
| `inferia-auth-ui/src/main.tsx` | create | React entry |
| `inferia-auth-ui/src/App.tsx` | create | route table |
| `inferia-auth-ui/src/styles/tokens.css` | create | CSS vars for brand |
| `inferia-auth-ui/src/components/{Logo,Button,Input,Card,ThemeToggle,Watermark}.tsx` | create | UI primitives |
| `inferia-auth-ui/src/routes/{Login,Error,Logout,ForgotPassword}.tsx` | create | pages |
| `inferia-auth-ui/src/lib/api.ts` | create | auth client |
| `inferia-auth-ui/src/lib/queryParams.ts` | create | safe `return_to` |
| `inferia-auth-ui/scripts/brand-check.ts` | create | banned-word linter |
| `inferia-auth-ui/public/inferia-icon-light.svg` | create | logo |
| `inferia-auth-ui/public/inferia-icon-dark.svg` | create | logo |
| `Dockerfile` | modify | multi-stage: build UI, copy `dist/` into Go image |
| `internal/transport/rest/router.go` | modify | static mount `/ui/*` + SPA fallback + `/login` redirect |

### Phase C — InferiaLLM gateway

| Path | Action | Purpose |
|---|---|---|
| `package/src/inferia/services/api_gateway/rbac/jwks_verifier.py` | create | cached JWKS verifier |
| `package/src/inferia/services/api_gateway/rbac/oauth_client.py` | create | `/oauth/token`, `/oauth/revoke` HTTP client |
| `package/src/inferia/services/api_gateway/rbac/oauth_router.py` | create | `/auth/start`, `/auth/callback` |
| `package/src/inferia/services/api_gateway/rbac/middleware.py` | modify | swap introspect for JWKS verify |
| `package/src/inferia/services/api_gateway/rbac/router.py` | modify | gate non-superadmin local login |
| `package/src/inferia/services/api_gateway/config.py` | modify | new env vars (§9.5 of spec) |
| `package/src/inferia/services/api_gateway/app.py` | modify | register `oauth_router` |
| `.env.sample` | modify | document new vars |
| `package/src/inferia/services/api_gateway/tests/test_jwks_verifier.py` | create | unit tests |
| `package/src/inferia/services/api_gateway/tests/test_oauth_router.py` | create | unit tests |
| `package/src/inferia/services/api_gateway/tests/test_middleware_external.py` | create | integration |

### Phase D — InferiaLLM dashboard

| Path | Action | Purpose |
|---|---|---|
| `apps/dashboard/src/services/authService.ts` | modify | redirect to `/auth/start`, parse `#access_token` |
| `apps/dashboard/src/pages/Login.tsx` | modify | render "Sign in with Inferia" button when external |
| `apps/dashboard/src/lib/tokenStore.ts` | modify | accept access from URL fragment |
| `apps/dashboard/.env.sample` | create-or-modify | `VITE_AUTH_PROVIDER` |

### Phase E — Docker compose & smoke

| Path | Action | Purpose |
|---|---|---|
| `deploy/docker-compose.sso.yml` | create | inferia-auth + inferia-app + caddy + postgres |
| `deploy/Caddyfile.sso` | create | reverse proxy two hostnames |
| `scripts/sso_smoke.sh` | create | bring-up + Playwright drive + tear-down |
| `scripts/sso_smoke.spec.ts` | create | browser steps |
| `docs/operations/auth.md` | create | operator runbook |

---

## Phase A: inferia-auth backend OAuth scaffolding

Work in `/storage/intern/hooman/work/inferia-auth/`.

### Task A1: Postgres migrations

**Files:**
- Create: `migrations/000010_oauth_clients.{up,down}.sql`
- Create: `migrations/000011_oauth_codes.{up,down}.sql`
- Create: `migrations/000012_oauth_refresh_tokens.{up,down}.sql`
- Create: `migrations/000013_oauth_sessions.{up,down}.sql`

- [ ] **Step 1: Write `000010_oauth_clients.up.sql`** using DDL from spec §5.1.

```sql
CREATE TABLE oauth_clients (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  client_id     TEXT NOT NULL UNIQUE,
  client_name   TEXT NOT NULL,
  app_namespace TEXT NOT NULL,
  client_type   TEXT NOT NULL CHECK (client_type IN ('public', 'confidential')),
  client_secret_hash TEXT,
  redirect_uris TEXT[] NOT NULL,
  allowed_scopes TEXT[] NOT NULL DEFAULT ARRAY['openid','profile','email'],
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX oauth_clients_app_namespace ON oauth_clients(app_namespace);
```

- [ ] **Step 2: Write the matching `.down.sql`** (`DROP TABLE oauth_clients;`).

- [ ] **Step 3: Write `000011_oauth_codes.up.sql`** (use spec §5.1 schema; key columns: `code TEXT PRIMARY KEY, client_id, user_id, redirect_uri, scopes TEXT[], code_challenge, code_challenge_method TEXT CHECK = 'S256', expires_at, used_at, created_at`). Add `CREATE INDEX oauth_codes_expires_at ON oauth_codes(expires_at);`.

- [ ] **Step 4: Write `000011_oauth_codes.down.sql`**.

- [ ] **Step 5: Write `000012_oauth_refresh_tokens.up.sql`** (spec §5.1: `id, token_hash, client_id, user_id, scopes, expires_at, revoked_at, parent_id`, with `parent_id REFERENCES oauth_refresh_tokens(id)` for rotation chain). Index on `user_id`.

- [ ] **Step 6: Write `000012_oauth_refresh_tokens.down.sql`**.

- [ ] **Step 7: Write `000013_oauth_sessions.up.sql`** (`id, session_key, user_id, expires_at, created_at` per spec).

- [ ] **Step 8: Write `000013_oauth_sessions.down.sql`**.

- [ ] **Step 9: Verify migrations run cleanly:**

```bash
cd /storage/intern/hooman/work/inferia-auth
docker compose up -d postgres
migrate -path migrations -database "$DATABASE_URL" up
psql "$DATABASE_URL" -c "\d oauth_clients" -c "\d oauth_codes" -c "\d oauth_refresh_tokens" -c "\d oauth_sessions"
```

Expected: all four tables print with correct columns.

- [ ] **Step 10: Roll back, then re-apply:**

```bash
migrate -path migrations -database "$DATABASE_URL" down 4
migrate -path migrations -database "$DATABASE_URL" up
```

Expected: idempotent.

- [ ] **Step 11: Commit**

```bash
git -c user.signingkey=/home/ankit/.ssh/id_ed25519_gh -c gpg.format=ssh \
    commit -S -m "feat(oauth): add postgres tables for codes, clients, refresh tokens, sessions"
```

### Task A2: FGA model extension + scope expansion usecase

**Files:**
- Modify: `openfga/model.fga`
- Create: `internal/usecase/oauth/scopes.go`
- Create: `internal/usecase/oauth/scopes_test.go`
- Create: `internal/usecase/oauth/seed_apps.go`
- Create: `internal/usecase/oauth/seed_apps_test.go`

- [ ] **Step 1: Add new FGA types to `openfga/model.fga`**

```fga
type app
  relations
    define has_permission_set: [permission_set]

type permission_set
  relations
    define grants_to_role: [role]
    define defines: [permission]

type permission
```

- [ ] **Step 2: Write failing test `scopes_test.go`**

```go
package oauth_test

import (
    "context"
    "testing"
    "github.com/inferia/inferia-auth/internal/usecase/oauth"
    "github.com/stretchr/testify/assert"
    "github.com/stretchr/testify/require"
)

func TestExpandPermissions_AdminInInferiallm(t *testing.T) {
    ctx := context.Background()
    fga := newTestFGAStore(t)  // helper bootstraps an in-memory FGA store with seeded model
    seed := oauth.NewSeeder(fga)
    require.NoError(t, seed.SeedApp(ctx, "inferiallm", map[string][]string{
        "admin": {"inferiallm:deployment:read", "inferiallm:deployment:write"},
        "member": {"inferiallm:deployment:read"},
    }))
    fga.GrantRole(ctx, "user:01HX", "admin", "organization:org1")
    svc := oauth.NewScopeService(fga)
    perms, err := svc.ExpandPermissions(ctx, "user:01HX", "inferiallm")
    require.NoError(t, err)
    assert.ElementsMatch(t, []string{
        "inferiallm:deployment:read",
        "inferiallm:deployment:write",
    }, perms)
}

func TestExpandPermissions_UnknownAppNamespace(t *testing.T) {
    ctx := context.Background()
    fga := newTestFGAStore(t)
    svc := oauth.NewScopeService(fga)
    perms, err := svc.ExpandPermissions(ctx, "user:01HX", "ghost-app")
    require.NoError(t, err)
    assert.Empty(t, perms)
}

func TestExpandPermissions_NoRoles(t *testing.T) {
    ctx := context.Background()
    fga := newTestFGAStore(t)
    seed := oauth.NewSeeder(fga)
    require.NoError(t, seed.SeedApp(ctx, "inferiallm", map[string][]string{"admin": {"inferiallm:audit:read"}}))
    svc := oauth.NewScopeService(fga)
    perms, err := svc.ExpandPermissions(ctx, "user:01HY", "inferiallm")
    require.NoError(t, err)
    assert.Empty(t, perms)
}

// EDGE: input length overflow
func TestExpandPermissions_AppNamespaceTooLong(t *testing.T) {
    svc := oauth.NewScopeService(newTestFGAStore(t))
    _, err := svc.ExpandPermissions(context.Background(), "user:x", strings.Repeat("a", 257))
    assert.ErrorIs(t, err, oauth.ErrInvalidAppNamespace)
}
```

- [ ] **Step 3: Run test, expect FAIL** (`go test ./internal/usecase/oauth/... -run ExpandPermissions`): undefined `oauth.NewScopeService`.

- [ ] **Step 4: Implement `scopes.go`**

```go
package oauth

import (
    "context"
    "errors"
    "regexp"
)

var (
    ErrInvalidAppNamespace = errors.New("invalid app namespace")
    appNamespaceRe         = regexp.MustCompile(`^[a-z][a-z0-9-]{0,63}$`)
)

type FGAStore interface {
    ListRolesForUser(ctx context.Context, userID string) ([]string, error)
    ListPermissionsForRoleInApp(ctx context.Context, role, appNamespace string) ([]string, error)
}

type ScopeService struct{ fga FGAStore }

func NewScopeService(fga FGAStore) *ScopeService { return &ScopeService{fga: fga} }

func (s *ScopeService) ExpandPermissions(ctx context.Context, userID, appNamespace string) ([]string, error) {
    if !appNamespaceRe.MatchString(appNamespace) {
        return nil, ErrInvalidAppNamespace
    }
    roles, err := s.fga.ListRolesForUser(ctx, userID)
    if err != nil {
        return nil, err
    }
    seen := map[string]struct{}{}
    out := []string{}
    for _, r := range roles {
        perms, err := s.fga.ListPermissionsForRoleInApp(ctx, r, appNamespace)
        if err != nil { return nil, err }
        for _, p := range perms {
            if _, ok := seen[p]; ok { continue }
            seen[p] = struct{}{}
            out = append(out, p)
        }
    }
    return out, nil
}
```

- [ ] **Step 5: Implement `seed_apps.go`**

```go
package oauth

import "context"

type FGASeederStore interface {
    WriteTuple(ctx context.Context, user, relation, object string) error
}
type Seeder struct{ fga FGASeederStore }
func NewSeeder(s FGASeederStore) *Seeder { return &Seeder{fga: s} }

// SeedApp creates an app:<ns>, one permission_set per role, and tuples linking
// roles to permission_sets and permissions to permission_sets.
// Idempotent: WriteTuple must treat ALREADY_EXISTS as success.
func (s *Seeder) SeedApp(ctx context.Context, appNamespace string, rolePerms map[string][]string) error {
    if !appNamespaceRe.MatchString(appNamespace) {
        return ErrInvalidAppNamespace
    }
    for role, perms := range rolePerms {
        ps := "permission_set:" + appNamespace + "-" + role
        if err := s.fga.WriteTuple(ctx, ps, "has_permission_set", "app:"+appNamespace); err != nil { return err }
        if err := s.fga.WriteTuple(ctx, "role:"+role, "grants_to_role", ps); err != nil { return err }
        for _, p := range perms {
            if err := s.fga.WriteTuple(ctx, "permission:"+p, "defines", ps); err != nil { return err }
        }
    }
    return nil
}
```

- [ ] **Step 6: Implement a `newTestFGAStore` helper** in `internal/usecase/oauth/testing.go` (test-build-tagged file) — in-memory map-backed implementation of the interfaces above. Same package, exported `_test.go` helpers.

- [ ] **Step 7: Run tests** (`go test ./internal/usecase/oauth/...`). Expected: PASS.

- [ ] **Step 8: Add a `seed_apps_test.go`** that calls `SeedApp` twice with identical input — second call must succeed without error (idempotent).

- [ ] **Step 9: Run all unit tests** (`go test ./...`). Expected: PASS.

- [ ] **Step 10: Commit**

```bash
git -c user.signingkey=/home/ankit/.ssh/id_ed25519_gh -c gpg.format=ssh \
    commit -S -m "feat(oauth): FGA app/permission_set types + scope expansion"
```

### Task A3: PKCE verifier

**Files:**
- Create: `internal/usecase/oauth/pkce.go`
- Create: `internal/usecase/oauth/pkce_test.go`

- [ ] **Step 1: Write failing test `pkce_test.go`**

```go
package oauth

import (
    "crypto/sha256"
    "encoding/base64"
    "strings"
    "testing"
    "github.com/stretchr/testify/assert"
)

func challenge(t *testing.T, verifier string) string {
    t.Helper()
    h := sha256.Sum256([]byte(verifier))
    return base64.RawURLEncoding.EncodeToString(h[:])
}

func TestVerifyChallenge_HappyPath(t *testing.T) {
    v := "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
    c := challenge(t, v)
    assert.NoError(t, VerifyChallenge(v, c, "S256"))
}

func TestVerifyChallenge_Mismatch(t *testing.T) {
    err := VerifyChallenge("verifier-a", challenge(t, "verifier-b"), "S256")
    assert.ErrorIs(t, err, ErrPKCEMismatch)
}

func TestVerifyChallenge_PlainRejected(t *testing.T) {
    err := VerifyChallenge("verifier", "verifier", "plain")
    assert.ErrorIs(t, err, ErrUnsupportedPKCEMethod)
}

func TestVerifyChallenge_EmptyVerifier(t *testing.T) {
    err := VerifyChallenge("", "anything", "S256")
    assert.ErrorIs(t, err, ErrVerifierTooShort)
}

// EDGE: RFC 7636 caps verifier at 43-128 chars.
func TestVerifyChallenge_VerifierTooShort(t *testing.T) {
    err := VerifyChallenge(strings.Repeat("a", 42), "x", "S256")
    assert.ErrorIs(t, err, ErrVerifierTooShort)
}

func TestVerifyChallenge_VerifierTooLong(t *testing.T) {
    err := VerifyChallenge(strings.Repeat("a", 129), "x", "S256")
    assert.ErrorIs(t, err, ErrVerifierTooLong)
}

// EDGE: malformed challenge string (e.g. has padding) — base64 raw url has no padding
func TestVerifyChallenge_ChallengeWithPaddingRejected(t *testing.T) {
    err := VerifyChallenge(strings.Repeat("a", 43), "anything==", "S256")
    assert.ErrorIs(t, err, ErrInvalidChallenge)
}
```

- [ ] **Step 2: Run test, expect FAIL** (`go test ./internal/usecase/oauth/ -run VerifyChallenge`).

- [ ] **Step 3: Implement `pkce.go`**

```go
package oauth

import (
    "crypto/sha256"
    "crypto/subtle"
    "encoding/base64"
    "errors"
    "strings"
)

var (
    ErrUnsupportedPKCEMethod = errors.New("unsupported code_challenge_method")
    ErrPKCEMismatch          = errors.New("PKCE challenge mismatch")
    ErrVerifierTooShort      = errors.New("code_verifier too short")
    ErrVerifierTooLong       = errors.New("code_verifier too long")
    ErrInvalidChallenge      = errors.New("invalid code_challenge format")
)

func VerifyChallenge(verifier, challenge, method string) error {
    if method != "S256" { return ErrUnsupportedPKCEMethod }
    if len(verifier) < 43 { return ErrVerifierTooShort }
    if len(verifier) > 128 { return ErrVerifierTooLong }
    if strings.Contains(challenge, "=") { return ErrInvalidChallenge }
    h := sha256.Sum256([]byte(verifier))
    expected := base64.RawURLEncoding.EncodeToString(h[:])
    if subtle.ConstantTimeCompare([]byte(expected), []byte(challenge)) != 1 {
        return ErrPKCEMismatch
    }
    return nil
}
```

- [ ] **Step 4: Run all 7 tests** — expected PASS.

- [ ] **Step 5: Commit**

```bash
git -c user.signingkey=/home/ankit/.ssh/id_ed25519_gh -c gpg.format=ssh \
    commit -S -m "feat(oauth): PKCE S256 verifier with constant-time compare"
```

### Task A4: Domain types + Postgres repositories

**Files:**
- Create: `internal/domain/oauth/{code,client,refresh,session}.go`
- Create: `internal/infrastructure/repository/oauth_{clients,codes,refresh,sessions}_postgres.go`
- Create: `internal/infrastructure/repository/oauth_*_test.go`

- [ ] **Step 1: Define domain types.** One struct per file:

```go
// internal/domain/oauth/code.go
package oauth
import "time"
type Code struct {
    Code, ClientID, UserID, RedirectURI string
    Scopes                              []string
    CodeChallenge, CodeChallengeMethod  string
    ExpiresAt, CreatedAt                time.Time
    UsedAt                              *time.Time
}
```

Similarly for `Client` (with `ClientType` enum: `Public`, `Confidential`), `RefreshToken` (with `ParentID *uuid.UUID`, `RevokedAt *time.Time`), `SsoSession`.

- [ ] **Step 2: For each repo, write a failing test** that uses `testcontainers-go` to spin a real Postgres (the project already uses this pattern — see `internal/infrastructure/repository/user_postgres_test.go` for the template):

```go
func TestOAuthCodesPostgres_StoreAndConsume(t *testing.T) {
    db := setupTestDB(t)
    repo := NewOAuthCodesPostgres(db)
    code := &oauth.Code{
        Code: "test-code-1",
        ClientID: "client-x",
        UserID: uuid.New().String(),
        RedirectURI: "https://x/callback",
        Scopes: []string{"openid", "inferiallm"},
        CodeChallenge: "challenge-bytes",
        CodeChallengeMethod: "S256",
        ExpiresAt: time.Now().Add(60 * time.Second),
    }
    require.NoError(t, repo.Store(context.Background(), code))
    got, err := repo.Consume(context.Background(), "test-code-1")
    require.NoError(t, err)
    assert.Equal(t, code.UserID, got.UserID)
    // Second consume must fail — single-use.
    _, err = repo.Consume(context.Background(), "test-code-1")
    assert.ErrorIs(t, err, oauth.ErrCodeAlreadyUsed)
}

func TestOAuthCodesPostgres_Expired(t *testing.T) {
    db := setupTestDB(t)
    repo := NewOAuthCodesPostgres(db)
    code := &oauth.Code{
        Code: "expired", ClientID: "c", UserID: uuid.NewString(),
        RedirectURI: "https://x", Scopes: []string{"openid"},
        CodeChallenge: "ch", CodeChallengeMethod: "S256",
        ExpiresAt: time.Now().Add(-1 * time.Second),
    }
    require.NoError(t, repo.Store(context.Background(), code))
    _, err := repo.Consume(context.Background(), "expired")
    assert.ErrorIs(t, err, oauth.ErrCodeExpired)
}
```

- [ ] **Step 3: Implement `oauth_codes_postgres.go`** with `Store`, `Consume`. `Consume` is a single `UPDATE oauth_codes SET used_at=now() WHERE code=$1 AND used_at IS NULL AND expires_at>now() RETURNING ...` — atomic, no race.

- [ ] **Step 4: Apply same TDD pattern to `oauth_clients_postgres.go`** (`GetByClientID`, `Insert`, `Seed`), `oauth_refresh_postgres.go` (`Store`, `Consume`, `Revoke`, `RevokeChain` — when a revoked token is presented, walk `parent_id` chain and revoke everything), and `oauth_sessions_postgres.go` (`Create`, `Lookup`, `Delete`).

For each: write 3-5 failing tests, implement, verify, commit. Refresh-chain detection is the most subtle — test it explicitly:

```go
func TestOAuthRefreshPostgres_RotationDetectsReplay(t *testing.T) {
    db := setupTestDB(t)
    repo := NewOAuthRefreshPostgres(db)
    t1 := storeFresh(t, repo, "user-a")
    // Rotate: create child, mark parent revoked
    t2, err := repo.Rotate(context.Background(), t1.ID, "user-a")
    require.NoError(t, err)
    // Re-consume t1 — must fail and revoke the chain (t2 included).
    _, err = repo.Consume(context.Background(), t1.TokenHash)
    assert.ErrorIs(t, err, oauth.ErrRefreshReplay)
    after, _ := repo.GetByID(context.Background(), t2.ID)
    assert.NotNil(t, after.RevokedAt)
}
```

- [ ] **Step 5: Run all repo tests** (`go test ./internal/infrastructure/repository/...`). Expected: all PASS.

- [ ] **Step 6: Commit each repository as its own commit** (4 commits total):

```bash
git -c user.signingkey=/home/ankit/.ssh/id_ed25519_gh -c gpg.format=ssh \
    commit -S -m "feat(oauth): oauth_codes postgres repo with single-use semantics"
# repeat for clients, refresh, sessions
```

### Task A5: JWT extension — `aud`, `roles`, `permissions` claims

**Files:**
- Modify: `internal/infrastructure/token/jwt.go`
- Modify: `internal/infrastructure/token/jwt_test.go`

- [ ] **Step 1: Open `jwt.go`** — current `Issue(subject, email, orgIDs, ttl)` signature lives around line 62.

- [ ] **Step 2: Add a new signature `IssueAccessToken(args)`** taking a struct:

```go
type IssueAccessTokenArgs struct {
    Subject      string
    SubjectType  string
    Email        string
    Audience     string
    OrgID        string
    OrgIDs       []string
    Roles        []string
    Permissions  []string
    Scope        string
    TTL          time.Duration
}

func (s *Service) IssueAccessToken(args IssueAccessTokenArgs) (string, error) {
    now := time.Now().Unix()
    claims := jwt.MapClaims{
        "iss":         s.issuer,
        "sub":         args.SubjectType + ":" + args.Subject,
        "aud":         args.Audience,
        "iat":         now,
        "exp":         now + int64(args.TTL.Seconds()),
        "type":        "access",
        "email":       args.Email,
        "org_id":      args.OrgID,
        "org_ids":     args.OrgIDs,
        "roles":       args.Roles,
        "permissions": args.Permissions,
        "scope":       args.Scope,
    }
    token := jwt.NewWithClaims(jwt.SigningMethodEdDSA, claims)
    return token.SignedString(s.privateKey)
}
```

- [ ] **Step 3: Write failing test** asserting an issued token decodes with all claims:

```go
func TestIssueAccessToken_AllClaims(t *testing.T) {
    svc := newTestService(t)
    tok, err := svc.IssueAccessToken(IssueAccessTokenArgs{
        Subject: "01HX", SubjectType: "user", Email: "a@b.c",
        Audience: "inferiallm", OrgID: "org-1", OrgIDs: []string{"org-1"},
        Roles: []string{"admin"}, Permissions: []string{"inferiallm:deployment:read"},
        Scope: "openid inferiallm", TTL: 15 * time.Minute,
    })
    require.NoError(t, err)
    claims := decodeUnverified(t, tok)
    assert.Equal(t, "inferiallm", claims["aud"])
    assert.Equal(t, "user:01HX", claims["sub"])
    assert.Equal(t, "access", claims["type"])
    assert.Equal(t, []interface{}{"admin"}, claims["roles"])
    assert.Equal(t, []interface{}{"inferiallm:deployment:read"}, claims["permissions"])
    assert.Equal(t, "openid inferiallm", claims["scope"])
}
```

- [ ] **Step 4: Run test** — expected PASS (after step 2).

- [ ] **Step 5: Commit**

```bash
git -c user.signingkey=/home/ankit/.ssh/id_ed25519_gh -c gpg.format=ssh \
    commit -S -m "feat(jwt): IssueAccessToken with aud/roles/permissions/scope claims"
```

### Task A6: `/oauth/authorize` handler

**Files:**
- Create: `internal/usecase/oauth/authorize.go` + `authorize_test.go`
- Create: `internal/transport/rest/handler/oauth_authorize.go`
- Modify: `internal/transport/rest/router.go`

- [ ] **Step 1: Write failing test `authorize_test.go`** for the usecase:

```go
func TestAuthorize_HappyPath(t *testing.T) {
    f := newFixture(t)
    f.seedClient("inferiallm-dashboard", []string{"https://inferia.local/auth/callback"}, "inferiallm")
    f.seedUser("user:01HX")
    f.startSession("user:01HX")  // SSO cookie present
    code, err := f.svc.Authorize(context.Background(), AuthorizeRequest{
        ClientID: "inferiallm-dashboard",
        RedirectURI: "https://inferia.local/auth/callback",
        ResponseType: "code",
        Scopes: []string{"openid", "inferiallm"},
        State: "state-1",
        CodeChallenge: "challenge-bytes-43-chars-or-more-mock-aaaa",
        CodeChallengeMethod: "S256",
        SsoSessionKey: f.session,
    })
    require.NoError(t, err)
    assert.NotEmpty(t, code)
}

func TestAuthorize_NoSession_ReturnsLoginRequired(t *testing.T) { /* asserts ErrLoginRequired */ }
func TestAuthorize_BadRedirectURI_NotPrefixMatched(t *testing.T) { /* exact match required */ }
func TestAuthorize_UnknownClient(t *testing.T) { /* ErrUnknownClient */ }
func TestAuthorize_ResponseTypeMustBeCode(t *testing.T) {}
func TestAuthorize_ScopeNotAllowed(t *testing.T) { /* client.allowed_scopes filter */ }
func TestAuthorize_MissingState_Rejected(t *testing.T) {}  // CSRF defense
func TestAuthorize_MissingPKCEForPublicClient_Rejected(t *testing.T) {}
```

- [ ] **Step 2: Run tests, expect FAIL.**

- [ ] **Step 3: Implement `authorize.go`**

```go
package oauth

type AuthorizeRequest struct {
    ClientID, RedirectURI, ResponseType, State string
    Scopes                                     []string
    CodeChallenge, CodeChallengeMethod         string
    SsoSessionKey                              string
}

type AuthorizeService struct {
    clients ClientsRepo
    sessions SessionsRepo
    codes CodesRepo
    rng RandomSource  // *crypto/rand by default; mock in tests
}

func (a *AuthorizeService) Authorize(ctx context.Context, req AuthorizeRequest) (string, error) {
    if req.ResponseType != "code" { return "", ErrUnsupportedResponseType }
    if req.State == "" { return "", ErrMissingState }
    client, err := a.clients.GetByClientID(ctx, req.ClientID)
    if err != nil { return "", ErrUnknownClient }
    if !containsExact(client.RedirectURIs, req.RedirectURI) { return "", ErrBadRedirectURI }
    if client.ClientType == Public && req.CodeChallenge == "" { return "", ErrPKCERequired }
    if req.CodeChallengeMethod == "" { req.CodeChallengeMethod = "S256" }
    if req.CodeChallengeMethod != "S256" { return "", ErrUnsupportedPKCEMethod }
    allowed := filterScopes(client.AllowedScopes, req.Scopes)
    if len(allowed) == 0 { return "", ErrScopeNotAllowed }
    session, err := a.sessions.Lookup(ctx, req.SsoSessionKey)
    if err != nil || session == nil { return "", ErrLoginRequired }
    code := a.rng.URLSafe(32)
    err = a.codes.Store(ctx, &Code{
        Code: code, ClientID: req.ClientID, UserID: session.UserID,
        RedirectURI: req.RedirectURI, Scopes: allowed,
        CodeChallenge: req.CodeChallenge, CodeChallengeMethod: req.CodeChallengeMethod,
        ExpiresAt: time.Now().Add(60 * time.Second),
    })
    return code, err
}
```

- [ ] **Step 4: Run usecase tests** — expected PASS.

- [ ] **Step 5: Write HTTP handler `oauth_authorize.go`** — parses query, calls usecase, on `ErrLoginRequired` does 302 to `/login?return_to=<url-encoded original>`, on success 302 to `redirect_uri?code=X&state=Y`, on other errors 400 with OAuth error response (`error=<code>&error_description=...`).

- [ ] **Step 6: Register route in `router.go`.**

- [ ] **Step 7: Write handler-level integration test** (use Fiber's test utilities) covering: (a) no session → 302 to /login, (b) with session → 302 with code, (c) invalid client → 400 with `error=invalid_client`.

- [ ] **Step 8: Run handler tests** — expected PASS.

- [ ] **Step 9: Commit**

```bash
git -c user.signingkey=/home/ankit/.ssh/id_ed25519_gh -c gpg.format=ssh \
    commit -S -m "feat(oauth): /oauth/authorize endpoint with PKCE + SSO session lookup"
```

### Task A7: `/oauth/token` handler (code + refresh grants)

**Files:**
- Create: `internal/usecase/oauth/token_exchange.go` + `_test.go`
- Create: `internal/transport/rest/handler/oauth_token.go`

- [ ] **Step 1: Write failing tests `token_exchange_test.go`** covering:
  - `TestExchange_CodeHappyPath` — issues access + refresh tokens with correct claims
  - `TestExchange_PKCEFailure` — wrong verifier → ErrPKCEMismatch
  - `TestExchange_CodeAlreadyUsed` — second exchange of same code fails
  - `TestExchange_CodeExpired`
  - `TestExchange_WrongRedirectURI` — must match the one used during authorize
  - `TestExchange_RefreshHappyPath` — old refresh revoked, new pair issued
  - `TestExchange_RefreshReplayDetected` — re-using a revoked refresh kills chain
  - `TestExchange_UnsupportedGrantType` — `password`, `client_credentials`, gibberish all rejected
  - **Edge:** `TestExchange_CodeVerifierTooLong` — overflow protection
  - **Edge:** `TestExchange_RefreshTokenTooLong` — refuse before DB lookup

- [ ] **Step 2: Run, expect FAIL.**

- [ ] **Step 3: Implement `token_exchange.go`** with two methods: `ExchangeCode(ctx, req)` and `RefreshTokens(ctx, req)`. Both build `IssueAccessTokenArgs` by calling `scopeService.ExpandPermissions(user, client.AppNamespace)` first — so refresh always picks up FGA-current scopes.

- [ ] **Step 4: Run, expect PASS.**

- [ ] **Step 5: Implement HTTP handler `oauth_token.go`** — `application/x-www-form-urlencoded` body, dispatches on `grant_type`. Returns OAuth-standard JSON `{access_token, refresh_token, token_type, expires_in, scope}` or `{error, error_description}`.

- [ ] **Step 6: Handler-level tests** — happy POST returns 200 with parseable JWT in body; bad PKCE returns 400 with `error=invalid_grant`.

- [ ] **Step 7: Run + commit.**

```bash
git -c user.signingkey=/home/ankit/.ssh/id_ed25519_gh -c gpg.format=ssh \
    commit -S -m "feat(oauth): /oauth/token authorization_code + refresh_token grants"
```

### Task A8: `/oauth/userinfo`, `/oauth/revoke`, `/.well-known/openid-configuration`

**Files:**
- Create: `internal/transport/rest/handler/oauth_{userinfo,revoke}.go`
- Create: `internal/transport/rest/handler/oidc_discovery.go`
- Modify: `internal/transport/rest/router.go`

- [ ] **Step 1: Write failing tests** for each:
  - `userinfo`: bearer-token-protected; returns `{sub, email, name?}`
  - `revoke`: accepts `token, token_type_hint` in form body; on success returns 200 empty body (per RFC 7009)
  - `discovery`: returns JSON with `issuer, authorization_endpoint, token_endpoint, userinfo_endpoint, revocation_endpoint, jwks_uri, response_types_supported=[code], grant_types_supported=[authorization_code, refresh_token], code_challenge_methods_supported=[S256], token_endpoint_auth_methods_supported=[none]` (for public clients)

- [ ] **Step 2-6:** Implement each, run tests, commit.

```bash
git -c user.signingkey=/home/ankit/.ssh/id_ed25519_gh -c gpg.format=ssh \
    commit -S -m "feat(oauth): /oauth/userinfo, /oauth/revoke, OIDC discovery"
```

### Task A9: Boot-time seeding of `inferiallm-dashboard` client + apps

**Files:**
- Modify: `cmd/server/main.go`
- Create: `cmd/server/seed.go` + `seed_test.go`

- [ ] **Step 1: Add env-var reads** in main: `OAUTH_SEED_CLIENT_ID, OAUTH_SEED_REDIRECT_URI, OAUTH_SEED_APP_NAMESPACE` (defaults documented).

- [ ] **Step 2: Write a failing test** `seed_test.go` that bootstraps a Postgres + FGA, calls `Seed(ctx, cfg)`, asserts:
  - `oauth_clients` row for `inferiallm-dashboard` exists
  - FGA has `app:inferiallm` with permission_set tuples linking `admin` and `member` roles
  - Re-running `Seed` is idempotent

- [ ] **Step 3: Implement `seed.go`** using existing `OAuthClientsRepo.Seed` + `oauth.Seeder.SeedApp`. Permission catalog comes from a constant `InferiallmPermissions` map sourced from InferiaLLM's existing `PermissionEnum` list — keep it inline (one source of truth maintained by hand for v1; later we wire admin endpoint).

- [ ] **Step 4: Run test + commit.**

```bash
git -c user.signingkey=/home/ankit/.ssh/id_ed25519_gh -c gpg.format=ssh \
    commit -S -m "feat(oauth): seed inferiallm-dashboard client + FGA app on boot"
```

### Task A10: SSO session cookie middleware (issued during /auth/login)

**Files:**
- Modify: `internal/transport/rest/handler/auth.go:73-105` (login handler)
- Create: `internal/usecase/auth/sso_session.go` + `_test.go`

- [ ] **Step 1: Failing test** — login response sets `Set-Cookie: inferia_auth_session=...; HttpOnly; Secure; SameSite=Lax; Path=/`, and a follow-up `/oauth/authorize` with that cookie skips the login prompt.

- [ ] **Step 2-5:** Implement, run, commit.

```bash
git -c user.signingkey=/home/ankit/.ssh/id_ed25519_gh -c gpg.format=ssh \
    commit -S -m "feat(oauth): SSO session cookie issued on /auth/login"
```

---

## Phase B: inferia-auth-ui (branded React app)

Work in `/storage/intern/hooman/work/inferia-auth/inferia-auth-ui/` (new directory).

### Task B1: Vite + Tailwind + brand tokens scaffold

**Files:**
- Create: `inferia-auth-ui/{package.json, vite.config.ts, tailwind.config.ts, postcss.config.js, tsconfig.json, index.html}`
- Create: `inferia-auth-ui/src/{main.tsx, App.tsx, styles/tokens.css, styles/global.css}`

- [ ] **Step 1: Initialize via npm** (operator runs):

```bash
cd /storage/intern/hooman/work/inferia-auth
npm create vite@latest inferia-auth-ui -- --template react-ts
cd inferia-auth-ui
npm install
npm install -D tailwindcss postcss autoprefixer
npm install react-router-dom
npx tailwindcss init -p
```

- [ ] **Step 2: Write `tailwind.config.ts`** referencing CSS variables:

```ts
import type { Config } from "tailwindcss";
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        "bg-primary": "var(--bg-primary)",
        "bg-secondary": "var(--bg-secondary)",
        "text-primary": "var(--text-primary)",
        "text-secondary": "var(--text-secondary)",
        "text-muted": "var(--text-muted)",
        accent: "var(--accent-warm)",
        "accent-soft": "var(--accent-soft)",
        border: "var(--border)",
        success: "var(--success)",
      },
      borderRadius: { card: "8px", control: "4px" },
      fontFamily: {
        sans: ['Inter', 'system-ui', 'sans-serif'],
        mono: ['"JetBrains Mono"', 'ui-monospace', 'monospace'],
      },
      boxShadow: { card: "0 1px 3px rgba(0,0,0,0.06)" },
    },
  },
  plugins: [],
} satisfies Config;
```

- [ ] **Step 3: Write `src/styles/tokens.css`** with both `:root` (light) and `[data-theme="dark"]` blocks from spec §4.1.

- [ ] **Step 4: Write `src/styles/global.css`**:

```css
@tailwind base;
@tailwind components;
@tailwind utilities;
@import "@fontsource/inter/index.css";
@import "@fontsource/jetbrains-mono/index.css";
@import "./tokens.css";

body { background: var(--bg-primary); color: var(--text-primary); font-family: var(--font-sans); }
```

- [ ] **Step 5: Build sanity** — `npm run build`. Expected: builds to `dist/` with no warnings.

- [ ] **Step 6: Commit**

```bash
git -c user.signingkey=/home/ankit/.ssh/id_ed25519_gh -c gpg.format=ssh \
    commit -S -m "feat(ui): inferia-auth-ui scaffold with brand tokens"
```

### Task B2: Brand component primitives

**Files:**
- Create: `inferia-auth-ui/src/components/{Logo,Button,Input,Card,ThemeToggle,Watermark}.tsx`
- Create: `inferia-auth-ui/src/components/__tests__/*.test.tsx` (vitest)
- Create: `inferia-auth-ui/public/inferia-icon-light.svg`, `inferia-icon-dark.svg`

- [ ] **Step 1: Generate brand icon SVGs.** Use a minimal "brain helmet" outline — neutral placeholder for now if assets aren't available, with TODO note in a separate `BRAND_ASSETS.md`:

```svg
<!-- public/inferia-icon-light.svg -->
<svg viewBox="0 0 64 64" xmlns="http://www.w3.org/2000/svg" fill="none" stroke="#1A1A1A" stroke-width="2">
  <path d="M32 8 L48 16 L48 40 C48 50 40 56 32 56 C24 56 16 50 16 40 L16 16 Z" />
  <path d="M24 24 Q32 30 40 24" />
  <path d="M24 34 Q32 40 40 34" />
</svg>
```

(Dark variant: same path, `stroke="#FFFFFF"`.)

- [ ] **Step 2: Write `Button.tsx`** with `variant: 'primary' | 'secondary' | 'ghost'`:

```tsx
type Props = { variant?: 'primary'|'secondary'|'ghost' } & React.ButtonHTMLAttributes<HTMLButtonElement>;
const base = "rounded-control px-6 py-3 text-sm font-medium font-sans transition-opacity duration-150";
const variants = {
  primary: "bg-accent text-white hover:opacity-90",
  secondary: "bg-transparent border border-border text-text-primary hover:bg-accent-soft",
  ghost: "bg-transparent text-text-primary hover:underline",
};
export const Button = ({ variant='primary', className='', ...rest }: Props) => (
  <button className={`${base} ${variants[variant]} ${className}`} {...rest} />
);
```

- [ ] **Step 3: Vitest unit test** asserting each variant renders the correct Tailwind classes + sets `type="button"` by default.

- [ ] **Step 4: Write `Input.tsx`** (4px radius, 16px font, focus-ember border) with matching test.

- [ ] **Step 5: Write `Card.tsx`** (8px radius, shadow) with test.

- [ ] **Step 6: Write `ThemeToggle.tsx`** — reads `localStorage["inferia-theme"]`, toggles `data-theme` on `<html>`. Test asserts toggle flips attribute and persists.

- [ ] **Step 7: Write `Logo.tsx`** — picks `light` or `dark` SVG based on current `data-theme`.

- [ ] **Step 8: Write `Watermark.tsx`** — fixed-position SVG at 8% opacity behind content.

- [ ] **Step 9: Run all component tests** (`npm test`). Expected: PASS.

- [ ] **Step 10: Commit**

```bash
git -c user.signingkey=/home/ankit/.ssh/id_ed25519_gh -c gpg.format=ssh \
    commit -S -m "feat(ui): brand-compliant Button/Input/Card/Logo/ThemeToggle primitives"
```

### Task B3: Routes — Login / Error / Logout / ForgotPassword

**Files:**
- Create: `inferia-auth-ui/src/routes/{Login,Error,Logout,ForgotPassword}.tsx`
- Create: `inferia-auth-ui/src/lib/{api,queryParams}.ts`
- Create: `inferia-auth-ui/src/routes/__tests__/*.test.tsx`

- [ ] **Step 1: Write `lib/queryParams.ts`** — safe `return_to` extractor that **only accepts same-origin URLs**:

```ts
export function getReturnTo(): string {
  const raw = new URLSearchParams(window.location.search).get('return_to');
  if (!raw) return '/';
  try {
    const url = new URL(raw, window.location.origin);
    if (url.origin !== window.location.origin) return '/';
    return url.pathname + url.search + url.hash;
  } catch { return '/'; }
}
```

Test cases: (a) `?return_to=/oauth/authorize?...` → returns the path, (b) `?return_to=https://evil.com` → returns `/`, (c) missing param → `/`, (d) malformed URL → `/`.

- [ ] **Step 2: Write `lib/api.ts`**:

```ts
export type LoginResult = { ok: true } | { ok: false; error: string };
export async function login(email: string, password: string): Promise<LoginResult> {
  const resp = await fetch('/api/v1/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    credentials: 'include',
    body: JSON.stringify({ email, password }),
  });
  if (resp.ok) return { ok: true };
  if (resp.status === 401 || resp.status === 403) {
    return { ok: false, error: "That email and password don't match. Try again." };
  }
  return { ok: false, error: "Sign in service is unavailable. Please try again in a moment." };
}
```

- [ ] **Step 3: Write `Login.tsx`**:

```tsx
import { useState } from 'react';
import { Button, Input, Card, Logo, ThemeToggle, Watermark } from '../components';
import { login } from '../lib/api';
import { getReturnTo } from '../lib/queryParams';

export function Login() {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function handle(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true); setError(null);
    const r = await login(email, password);
    if (r.ok) window.location.assign(getReturnTo());
    else { setError(r.error); setBusy(false); }
  }

  return (
    <div className="min-h-screen bg-bg-primary flex flex-col items-center justify-center p-6 relative">
      <Watermark />
      <ThemeToggle className="absolute top-6 right-6" />
      <div className="w-full max-w-[480px]">
        <div className="flex items-center gap-3 mb-8">
          <Logo />
          <span className="font-sans font-bold text-lg text-text-primary">Inferia</span>
        </div>
        <Card>
          <h1 className="text-2xl font-bold text-text-primary mb-2">Sign in</h1>
          <p className="text-text-secondary mb-6">Enter your email and password to continue.</p>
          <form onSubmit={handle} className="space-y-4">
            <Input type="email" placeholder="you@company.com"
                   value={email} onChange={e => setEmail(e.target.value)}
                   required autoComplete="email" autoFocus />
            <Input type="password" placeholder="Your password"
                   value={password} onChange={e => setPassword(e.target.value)}
                   required autoComplete="current-password" />
            {error && <p className="text-sm text-accent">{error}</p>}
            <Button type="submit" disabled={busy} className="w-full">
              {busy ? "Signing in..." : "Sign in"}
            </Button>
          </form>
          <div className="mt-6 text-sm">
            <a href="/forgot-password" className="text-text-muted hover:underline">
              Forgot your password?
            </a>
          </div>
        </Card>
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Write `Error.tsx`, `Logout.tsx`, `ForgotPassword.tsx`** with the same brand pattern. Error reads `?error=...&error_description=...` and shows a friendly message:

```tsx
const MESSAGES: Record<string, string> = {
  access_denied: "Sign in was cancelled. Try again whenever you're ready.",
  invalid_client: "Something looks misconfigured. Reach out to your administrator.",
  server_error: "Something went wrong on our side. Try again in a moment.",
};
```

(Reads from a const map — no banned-word phrases.)

- [ ] **Step 5: Write `App.tsx`** with react-router:

```tsx
import { BrowserRouter, Routes, Route } from 'react-router-dom';
import { Login, Error, Logout, ForgotPassword } from './routes';
export default function App() {
  return (
    <BrowserRouter basename="/ui">
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="/error" element={<Error />} />
        <Route path="/logout" element={<Logout />} />
        <Route path="/forgot-password" element={<ForgotPassword />} />
      </Routes>
    </BrowserRouter>
  );
}
```

- [ ] **Step 6: Vitest tests** for Login: (a) submitting empty form is blocked by HTML5 validation, (b) successful login redirects to `return_to`, (c) 401 shows the configured error string, (d) external redirect target is sanitized.

- [ ] **Step 7: Run tests + commit**

```bash
git -c user.signingkey=/home/ankit/.ssh/id_ed25519_gh -c gpg.format=ssh \
    commit -S -m "feat(ui): Login/Error/Logout/ForgotPassword routes"
```

### Task B4: Brand-check build hook

**Files:**
- Create: `inferia-auth-ui/scripts/brand-check.ts`
- Modify: `inferia-auth-ui/package.json` (build script chain)

- [ ] **Step 1: Write `scripts/brand-check.ts`**

```ts
import { readdirSync, readFileSync, statSync } from 'fs';
import { join } from 'path';

const BANNED_WORDS = ['revolutionizing', 'leveraging', 'transforming', 'journey'];
const BANNED_HYPHENS = [/\bself-hosted\b/i, /\bopen-weight\b/i, /\breal-time\b/i];

function walk(dir: string): string[] {
  return readdirSync(dir).flatMap(name => {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) return walk(p);
    if (!/\.(tsx?|css|html)$/.test(name)) return [];
    return [p];
  });
}

const errors: string[] = [];
for (const file of walk('src')) {
  const text = readFileSync(file, 'utf8');
  for (const w of BANNED_WORDS) {
    if (new RegExp(`\\b${w}\\b`, 'i').test(text)) errors.push(`${file}: banned word "${w}"`);
  }
  for (const re of BANNED_HYPHENS) {
    if (re.test(text)) errors.push(`${file}: hyphenated phrase ${re}`);
  }
}
if (errors.length) {
  console.error("brand-check failed:");
  for (const e of errors) console.error("  " + e);
  process.exit(1);
}
console.log("brand-check OK");
```

- [ ] **Step 2: Modify `package.json` build script**:

```json
{
  "scripts": {
    "brand-check": "tsx scripts/brand-check.ts",
    "build": "npm run brand-check && tsc -b && vite build",
    "test": "vitest run"
  }
}
```

- [ ] **Step 3: Verify it passes on current files** (`npm run brand-check`).

- [ ] **Step 4: Verify it fails on intentional violation** — add `// leveraging FGA` to a tsx file, re-run, confirm exit code != 0, revert.

- [ ] **Step 5: Commit**

```bash
git -c user.signingkey=/home/ankit/.ssh/id_ed25519_gh -c gpg.format=ssh \
    commit -S -m "feat(ui): brand-check build hook (banned words + hyphenated phrases)"
```

### Task B5: Mount UI in Go service + Dockerfile

**Files:**
- Modify: `internal/transport/rest/router.go`
- Modify: `Dockerfile`

- [ ] **Step 1: In `router.go`**, after existing routes:

```go
app.Get("/login", func(c fiber.Ctx) error {
    return c.Redirect("/ui/login" + (c.OriginalURL()[len("/login"):]))
})
app.Static("/ui", "./inferia-auth-ui/dist", fiber.Static{
    Index: "index.html",
    Browse: false,
})
// SPA fallback for client-side routes
app.Get("/ui/*", func(c fiber.Ctx) error {
    return c.SendFile("./inferia-auth-ui/dist/index.html")
})
```

- [ ] **Step 2: Modify `Dockerfile`** to a multi-stage build:

```dockerfile
# UI build stage
FROM node:20-alpine AS ui-build
WORKDIR /ui
COPY inferia-auth-ui/package*.json ./
RUN npm ci
COPY inferia-auth-ui/ ./
RUN npm run build

# Go build stage
FROM golang:1.22-alpine AS go-build
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY . ./
RUN CGO_ENABLED=0 go build -o /out/inferia-auth ./cmd/server

# Final image
FROM alpine:3.19
RUN apk add --no-cache ca-certificates
WORKDIR /app
COPY --from=go-build /out/inferia-auth ./inferia-auth
COPY --from=ui-build /ui/dist ./inferia-auth-ui/dist
EXPOSE 3000 50051
ENTRYPOINT ["/app/inferia-auth"]
```

- [ ] **Step 3: Build the image:**

```bash
docker build -t inferia-auth:dev .
docker run --rm -p 3000:3000 -e DATABASE_URL=... inferia-auth:dev &
curl -sf http://localhost:3000/ui/login | grep -q '<div id="root">' && echo OK
```

- [ ] **Step 4: Commit**

```bash
git -c user.signingkey=/home/ankit/.ssh/id_ed25519_gh -c gpg.format=ssh \
    commit -S -m "feat(ui): mount /ui/* in Go service + multi-stage Dockerfile"
```

---

## Phase C: InferiaLLM gateway changes

Work in `/storage/intern/hooman/work/InferiaLLM/`. **Same branch** (`feat/aws-ec2-provisioning`).

### Task C1: Config + env-var validation

**Files:**
- Modify: `package/src/inferia/services/api_gateway/config.py`
- Modify: `.env.sample`

- [ ] **Step 1: Add to `config.py`** under the existing `auth_provider` and `external_auth_url` fields:

```python
external_auth_issuer: Optional[str] = Field(
    default=None, validation_alias="EXTERNAL_AUTH_ISSUER",
    description="Expected 'iss' claim in inferia-auth-issued JWTs",
)
app_namespace: str = Field(
    default="inferiallm", validation_alias="APP_NAMESPACE",
)
oauth_client_id: Optional[str] = Field(default=None, validation_alias="OAUTH_CLIENT_ID")
oauth_redirect_uri: Optional[str] = Field(default=None, validation_alias="OAUTH_REDIRECT_URI")
oauth_jwks_cache_ttl_seconds: int = Field(
    default=3600, validation_alias="OAUTH_JWKS_CACHE_TTL_SECONDS", ge=60, le=86400,
)

@model_validator(mode="after")
def _validate_external_auth_complete(self):
    if self.auth_provider == "external":
        missing = [k for k, v in {
            "EXTERNAL_AUTH_URL": self.external_auth_url,
            "EXTERNAL_AUTH_ISSUER": self.external_auth_issuer,
            "OAUTH_CLIENT_ID": self.oauth_client_id,
            "OAUTH_REDIRECT_URI": self.oauth_redirect_uri,
        }.items() if not v]
        if missing:
            raise ValueError(f"AUTH_PROVIDER=external requires: {', '.join(missing)}")
    return self
```

- [ ] **Step 2: Update `.env.sample`** with the new vars + comments (copy from spec §9.5).

- [ ] **Step 3: Run** `pytest package/src/inferia/services/api_gateway/tests/test_config.py -v` (file should exist; if not, add one with a test asserting the validator rejects incomplete config and accepts complete config).

- [ ] **Step 4: Commit**

```bash
git -c user.signingkey=/home/ankit/.ssh/id_ed25519_gh -c gpg.format=ssh \
    commit -S -m "feat(gateway): config validation for external auth provider"
```

### Task C2: JWKS verifier (TDD)

**Files:**
- Create: `package/src/inferia/services/api_gateway/rbac/jwks_verifier.py`
- Create: `package/src/inferia/services/api_gateway/tests/test_jwks_verifier.py`

- [ ] **Step 1: Failing test file** — uses a fake JWKS HTTP server via `pytest-httpserver` and a real `cryptography` Ed25519 keypair:

```python
import pytest
import time
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PrivateFormat, NoEncryption
from jose import jwt as jose_jwt
import json
import base64

from inferia.services.api_gateway.rbac.jwks_verifier import JWKSVerifier, JWKSVerifyError

@pytest.fixture
def keypair():
    priv = Ed25519PrivateKey.generate()
    pub = priv.public_key()
    raw_pub = pub.public_bytes(encoding=Encoding.Raw, format=PrivateFormat.Raw)
    x = base64.urlsafe_b64encode(raw_pub).rstrip(b"=").decode()
    pem = priv.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()).decode()
    jwks = {"keys": [{"kty": "OKP", "crv": "Ed25519", "kid": "test-key", "use": "sig", "alg": "EdDSA", "x": x}]}
    return pem, jwks

def sign(pem: str, claims: dict, headers=None) -> str:
    return jose_jwt.encode(claims, pem, algorithm="EdDSA", headers={"kid": "test-key", **(headers or {})})

def test_verify_happy_path(httpserver, keypair):
    pem, jwks = keypair
    httpserver.expect_request("/.well-known/jwks.json").respond_with_json(jwks)
    now = int(time.time())
    token = sign(pem, {"iss": "https://auth.local", "aud": "inferiallm",
                       "sub": "user:01HX", "exp": now + 60, "iat": now,
                       "type": "access", "email": "a@b.c",
                       "roles": ["admin"], "permissions": ["inferiallm:audit:read"]})
    v = JWKSVerifier(jwks_url=httpserver.url_for("/.well-known/jwks.json"),
                     issuer="https://auth.local", audience="inferiallm")
    claims = v.verify_sync(token)
    assert claims["email"] == "a@b.c"
    assert claims["permissions"] == ["inferiallm:audit:read"]

def test_verify_expired(httpserver, keypair): ...   # raises JWKSVerifyError("expired")
def test_verify_wrong_iss(httpserver, keypair): ...
def test_verify_wrong_aud(httpserver, keypair): ...
def test_verify_missing_type(httpserver, keypair): ...
def test_verify_wrong_alg_rs256_rejected(httpserver, keypair): ...
def test_verify_signature_tampered(httpserver, keypair): ...
def test_verify_jwks_endpoint_unreachable(httpserver, keypair): ...  # connection refused
def test_verify_cache_hits_jwks_once(httpserver, keypair): ...       # 5 verifications → 1 GET
def test_verify_cache_refetches_after_ttl(httpserver, keypair): ...
def test_verify_clock_skew_tolerated(httpserver, keypair): ...       # exp 30s in past, skew 60s → OK
# EDGE: input length overflow
def test_verify_token_too_long_rejected(httpserver, keypair):
    v = JWKSVerifier(jwks_url=httpserver.url_for("/.well-known/jwks.json"),
                     issuer="https://auth.local", audience="inferiallm")
    with pytest.raises(JWKSVerifyError):
        v.verify_sync("a." * 5000)
```

- [ ] **Step 2: Run tests, expect FAIL.**

- [ ] **Step 3: Implement `jwks_verifier.py`**

```python
import time
import logging
from typing import Optional
import httpx
from jose import jwt as jose_jwt, JWTError
from jose.exceptions import ExpiredSignatureError

logger = logging.getLogger(__name__)

class JWKSVerifyError(Exception): pass

_MAX_TOKEN_LEN = 8192
_CLOCK_SKEW_SECONDS = 60

class JWKSVerifier:
    def __init__(self, *, jwks_url: str, issuer: str, audience: str,
                 cache_ttl: int = 3600, http_client: Optional[httpx.Client] = None):
        self._url = jwks_url
        self._issuer = issuer
        self._audience = audience
        self._cache_ttl = cache_ttl
        self._jwks: Optional[dict] = None
        self._cached_at: float = 0
        self._client = http_client or httpx.Client(timeout=5.0)

    def _fetch_jwks(self) -> dict:
        try:
            resp = self._client.get(self._url)
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError as e:
            raise JWKSVerifyError(f"JWKS fetch failed: {type(e).__name__}") from e

    def _get_jwks(self) -> dict:
        if self._jwks and (time.time() - self._cached_at) < self._cache_ttl:
            return self._jwks
        self._jwks = self._fetch_jwks()
        self._cached_at = time.time()
        return self._jwks

    def verify_sync(self, token: str) -> dict:
        if not token or len(token) > _MAX_TOKEN_LEN:
            raise JWKSVerifyError("token length out of range")
        jwks = self._get_jwks()
        try:
            claims = jose_jwt.decode(
                token, jwks, algorithms=["EdDSA"],
                audience=self._audience, issuer=self._issuer,
                options={"leeway": _CLOCK_SKEW_SECONDS},
            )
        except ExpiredSignatureError:
            raise JWKSVerifyError("expired")
        except JWTError as e:
            raise JWKSVerifyError(str(e))
        if claims.get("type") != "access":
            raise JWKSVerifyError("token type must be access")
        return claims

    async def verify(self, token: str) -> dict:
        # Sync path is used directly because verifying is CPU-bound + cached HTTP.
        # Keep async wrapper for future I/O changes.
        return self.verify_sync(token)
```

- [ ] **Step 4: Run, expect PASS** for all 11 tests.

- [ ] **Step 5: Commit**

```bash
git -c user.signingkey=/home/ankit/.ssh/id_ed25519_gh -c gpg.format=ssh \
    commit -S -m "feat(gateway): JWKSVerifier with cache, skew tolerance, length cap"
```

### Task C3: OAuth HTTP client (`/oauth/token`, `/oauth/revoke`)

**Files:**
- Create: `package/src/inferia/services/api_gateway/rbac/oauth_client.py`
- Create: `package/src/inferia/services/api_gateway/tests/test_oauth_client.py`

- [ ] **Step 1: Failing test** with `httpserver` mocking inferia-auth:
  - `test_exchange_code_happy_path`
  - `test_exchange_code_400_returns_none`
  - `test_exchange_code_network_error_raises`
  - `test_refresh_happy_path`
  - `test_revoke_returns_true_on_200`
  - **Edge:** `test_code_too_long_rejected_before_send` (code max 256 chars)

- [ ] **Step 2: Implement `oauth_client.py`** — async httpx client wrapping `POST /oauth/token` and `POST /oauth/revoke`, with explicit length caps on `code`, `code_verifier`, `refresh_token`.

- [ ] **Step 3: Run + commit.**

```bash
git -c user.signingkey=/home/ankit/.ssh/id_ed25519_gh -c gpg.format=ssh \
    commit -S -m "feat(gateway): oauth_client for token exchange and revocation"
```

### Task C4: `/auth/start` and `/auth/callback` router

**Files:**
- Create: `package/src/inferia/services/api_gateway/rbac/oauth_router.py`
- Create: `package/src/inferia/services/api_gateway/tests/test_oauth_router.py`
- Modify: `package/src/inferia/services/api_gateway/app.py`

- [ ] **Step 1: Failing tests** for `/auth/start`:
  - Returns 302 to `EXTERNAL_AUTH_URL/oauth/authorize?...` with required params + sets `oauth_state` and `oauth_verifier` httpOnly cookies
  - 503 when `auth_provider != external`
  - Generated state is at least 32 bytes URL-safe base64

For `/auth/callback`:
  - Happy path: exchange succeeds, sets `refresh_token` cookie, 302 to `/#access_token=...`
  - Mismatched state → 400
  - Missing verifier cookie → 400
  - Token exchange returns 400 → 502 to client (`Sign in service rejected the response`)
  - **Edge:** code param length > 256 → 400 before any network call

- [ ] **Step 2: Implement `oauth_router.py`** per spec §9.2.

- [ ] **Step 3: Register in `app.py`** alongside existing `rbac.router.router`.

- [ ] **Step 4: Run + commit.**

```bash
git -c user.signingkey=/home/ankit/.ssh/id_ed25519_gh -c gpg.format=ssh \
    commit -S -m "feat(gateway): /auth/start + /auth/callback with PKCE state cookies"
```

### Task C5: Middleware — swap introspect for JWKS verify

**Files:**
- Modify: `package/src/inferia/services/api_gateway/rbac/middleware.py:63-108` (the `_resolve_external_token` function)
- Modify: `package/src/inferia/services/api_gateway/tests/test_middleware_external.py` (existing or new)

- [ ] **Step 1: Read current `_resolve_external_token`** (lines 63-108) — it calls `external_introspect` + `get_or_create_shadow_user` and assigns local DB-resolved roles.

- [ ] **Step 2: Write failing tests** for the new behavior:
  - Token verifies via JWKS → `UserContext` has roles/permissions from JWT claims, not from local DB
  - Token expired → 401
  - Token wrong audience → 401
  - Shadow user gets created if email is new
  - **Edge:** existing local user (same email) gets re-used (no duplicate row)

- [ ] **Step 3: Rewrite `_resolve_external_token`** to use `JWKSVerifier` (instantiated lazily as a module-level singleton via settings):

```python
from inferia.services.api_gateway.rbac.jwks_verifier import JWKSVerifier, JWKSVerifyError

_verifier: Optional[JWKSVerifier] = None

def _get_verifier() -> JWKSVerifier:
    global _verifier
    if _verifier is None:
        _verifier = JWKSVerifier(
            jwks_url=settings.external_auth_url.rstrip("/") + "/.well-known/jwks.json",
            issuer=settings.external_auth_issuer,
            audience=settings.app_namespace,
            cache_ttl=settings.oauth_jwks_cache_ttl_seconds,
        )
    return _verifier

async def _resolve_external_token(db, token: str) -> UserContext:
    try:
        claims = _get_verifier().verify_sync(token)
    except JWKSVerifyError as e:
        raise HTTPException(401, detail=str(e), headers={"WWW-Authenticate": "Bearer"})
    sub = claims["sub"]
    external_user_id = sub.split(":", 1)[1] if ":" in sub else sub
    user, _, _ = await get_or_create_shadow_user(
        db, email=claims.get("email", ""), external_id=external_user_id,
    )
    return UserContext(
        user_id=user.id, username=user.email, email=user.email,
        roles=claims.get("roles", []),
        permissions=claims.get("permissions", []),
        org_id=claims.get("org_id"),
        quota_limit=10000, quota_used=0,
    )
```

- [ ] **Step 4: Run tests + commit.**

```bash
git -c user.signingkey=/home/ankit/.ssh/id_ed25519_gh -c gpg.format=ssh \
    commit -S -m "feat(gateway): middleware verifies inferia-auth JWT via JWKS, reads claim roles"
```

### Task C6: Gate non-superadmin local login

**Files:**
- Modify: `package/src/inferia/services/api_gateway/rbac/router.py:59-139` (login handler)
- Modify: `package/src/inferia/services/api_gateway/tests/test_auth_router.py` (existing)

- [ ] **Step 1: Failing test** — under `AUTH_PROVIDER=external`, `POST /auth/login` with a non-superadmin email returns 403 with body `{"detail": "Direct password sign in is disabled. Use /auth/start."}` (note brand-friendly wording).

- [ ] **Step 2: Add the gate at the top of `login()`** (before the existing rate limiter):

```python
use_external = _settings.auth_provider == "external" and _settings.external_auth_url
if use_external and request.username != _settings.superadmin_email:
    raise HTTPException(
        status_code=403,
        detail="Direct password sign in is disabled. Use /auth/start.",
    )
```

- [ ] **Step 3: Run + commit.**

```bash
git -c user.signingkey=/home/ankit/.ssh/id_ed25519_gh -c gpg.format=ssh \
    commit -S -m "feat(gateway): block non-superadmin local password login when external"
```

---

## Phase D: InferiaLLM dashboard

### Task D1: Auth service redirect flow

**Files:**
- Modify: `apps/dashboard/src/services/authService.ts`
- Modify: `apps/dashboard/src/lib/tokenStore.ts`
- Modify: `apps/dashboard/.env.sample` (create if missing)

- [ ] **Step 1: Read current `authService.ts`** (it has `login(email, pass)` posting to `/auth/login`).

- [ ] **Step 2: Add a new function** `startExternalLogin()`:

```ts
export function startExternalLogin() {
  window.location.assign('/auth/start');
}

export function consumeAccessTokenFragment(): string | null {
  if (!window.location.hash) return null;
  const params = new URLSearchParams(window.location.hash.slice(1));
  const token = params.get('access_token');
  if (token) history.replaceState(null, '', window.location.pathname + window.location.search);
  return token;
}
```

- [ ] **Step 3: In `tokenStore.ts`**, add `setAccessToken(token: string)` that writes to in-memory holder. On app mount in `App.tsx`, call:

```ts
useEffect(() => {
  const t = consumeAccessTokenFragment();
  if (t) tokenStore.setAccessToken(t);
}, []);
```

- [ ] **Step 4: Add `VITE_AUTH_PROVIDER`** to `.env.sample` and a runtime check `if (import.meta.env.VITE_AUTH_PROVIDER === 'external')` to gate which login button is shown.

- [ ] **Step 5: Vitest unit test** for `consumeAccessTokenFragment` covering: token present, token absent, malformed hash.

- [ ] **Step 6: Commit.**

```bash
git -c user.signingkey=/home/ankit/.ssh/id_ed25519_gh -c gpg.format=ssh \
    commit -S -m "feat(dashboard): startExternalLogin + access token URL fragment consumer"
```

### Task D2: Dashboard login page — Sign in with Inferia

**Files:**
- Modify: `apps/dashboard/src/pages/Login.tsx`

- [ ] **Step 1: Add the redirect button when external mode is detected**:

```tsx
const isExternal = import.meta.env.VITE_AUTH_PROVIDER === 'external';
return (
  <Card>
    {isExternal ? (
      <>
        <h1>Sign in</h1>
        <p>You'll be redirected to your organization's identity provider.</p>
        <Button onClick={startExternalLogin}>Sign in with Inferia</Button>
      </>
    ) : (
      <LocalCredentialForm />   {/* existing form, refactored into its own component */}
    )}
  </Card>
);
```

- [ ] **Step 2: Vitest test** that asserts:
  - `VITE_AUTH_PROVIDER=external` → renders the redirect button, no email/password inputs visible
  - `VITE_AUTH_PROVIDER=local` (or unset) → renders the existing form
  - Clicking the button calls `startExternalLogin` (mocked)

- [ ] **Step 3: Run + commit.**

```bash
git -c user.signingkey=/home/ankit/.ssh/id_ed25519_gh -c gpg.format=ssh \
    commit -S -m "feat(dashboard): Sign in with Inferia button when AUTH_PROVIDER=external"
```

### Task D3: Logout flow

**Files:**
- Modify: `apps/dashboard/src/services/authService.ts`

- [ ] **Step 1: Add `logout()`** that POSTs to gateway `/auth/logout` (existing endpoint), then in external mode also navigates to `EXTERNAL_AUTH_URL/logout?post_logout_redirect_uri=<dashboard origin>/login`.

- [ ] **Step 2: Vitest test** covering both modes.

- [ ] **Step 3: Commit.**

```bash
git -c user.signingkey=/home/ankit/.ssh/id_ed25519_gh -c gpg.format=ssh \
    commit -S -m "feat(dashboard): logout flow with external-mode IdP redirect"
```

---

## Phase E: Docker compose + smoke test

### Task E1: Compose file + Caddy reverse proxy

**Files:**
- Create: `deploy/docker-compose.sso.yml`
- Create: `deploy/Caddyfile.sso`
- Modify: `Makefile` (add `docker-up-sso` target)

- [ ] **Step 1: Write `deploy/docker-compose.sso.yml`** per spec §10.

- [ ] **Step 2: Write `deploy/Caddyfile.sso`** with both hostnames + `tls internal` for local-dev certs.

- [ ] **Step 3: Add Makefile target**:

```makefile
docker-up-sso:
	docker compose -f deploy/docker-compose.unified.yml -f deploy/docker-compose.sso.yml up --build -d
docker-down-sso:
	docker compose -f deploy/docker-compose.unified.yml -f deploy/docker-compose.sso.yml down
```

- [ ] **Step 4: Document `/etc/hosts` entries** in `docs/operations/auth.md`:

```
127.0.0.1 inferia.local
127.0.0.1 auth.inferia.local
```

- [ ] **Step 5: Bring it up** (manual):

```bash
make docker-up-sso
curl -sk https://inferia.local/health | jq .
curl -sk https://auth.inferia.local/health | jq .
```

Both must return `{"status": "ok"}` (or whatever existing health endpoints return).

- [ ] **Step 6: Commit.**

```bash
git -c user.signingkey=/home/ankit/.ssh/id_ed25519_gh -c gpg.format=ssh \
    commit -S -m "feat(deploy): docker-compose.sso + caddy reverse proxy"
```

### Task E2: Playwright smoke test

**Files:**
- Create: `scripts/sso_smoke.sh`
- Create: `scripts/sso_smoke.spec.ts`
- Create: `scripts/package.json`, `playwright.config.ts`

- [ ] **Step 1: Write `scripts/sso_smoke.spec.ts`**:

```ts
import { test, expect } from '@playwright/test';

test('SSO login → protected endpoint', async ({ page }) => {
  // 1. Hit dashboard root → redirected to /login
  await page.goto('https://inferia.local/');
  await expect(page).toHaveURL(/.*\/login/);

  // 2. Click "Sign in with Inferia"
  await page.getByRole('button', { name: 'Sign in with Inferia' }).click();

  // 3. Arrives at inferia-auth-ui/login
  await expect(page).toHaveURL(/auth\.inferia\.local\/ui\/login/);

  // 4. Submit seeded test creds
  await page.getByPlaceholder('you@company.com').fill('smoke@inferia.local');
  await page.getByPlaceholder('Your password').fill('smoke-password');
  await page.getByRole('button', { name: 'Sign in' }).click();

  // 5. Lands back on dashboard
  await expect(page).toHaveURL(/inferia\.local\/(#access_token=)?/);

  // 6. Hit a protected endpoint
  const resp = await page.request.get('https://inferia.local/auth/me');
  expect(resp.status()).toBe(200);
  const body = await resp.json();
  expect(body.email).toBe('smoke@inferia.local');
});
```

- [ ] **Step 2: Write `scripts/sso_smoke.sh`**:

```bash
#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

echo "===> compose up"
make docker-up-sso

echo "===> waiting for services"
for url in https://inferia.local/health https://auth.inferia.local/health; do
  for i in {1..30}; do
    if curl -sfk "$url" >/dev/null; then break; fi
    sleep 2
  done
done

echo "===> seeding smoke user in inferia-auth"
docker compose -f deploy/docker-compose.sso.yml exec -T inferia-auth \
  /app/inferia-auth seed-user --email smoke@inferia.local --password smoke-password --role admin

echo "===> running playwright"
cd scripts && npm install --silent && npx playwright install --with-deps chromium >/dev/null
npx playwright test sso_smoke.spec.ts

echo "===> compose down"
cd .. && make docker-down-sso

echo "ALL GOOD"
```

- [ ] **Step 3: Run end-to-end:**

```bash
bash scripts/sso_smoke.sh
```

Expected: prints `ALL GOOD`, no Playwright failures.

- [ ] **Step 4: Commit.**

```bash
git -c user.signingkey=/home/ankit/.ssh/id_ed25519_gh -c gpg.format=ssh \
    commit -S -m "feat(deploy): SSO smoke test (compose up + playwright)"
```

### Task E3: Operator runbook

**Files:**
- Create: `docs/operations/auth.md`

- [ ] **Step 1: Write the runbook** covering:
  - How to flip `AUTH_PROVIDER` (env, restart, what changes)
  - `/etc/hosts` entries for local dev
  - How to seed an admin in inferia-auth (CLI command)
  - How to rotate JWKS keys (operator procedure: generate new ed25519, restart inferia-auth, keep old key in JWKS for one TTL window)
  - Superadmin lockout recovery (`inferiallm reset-superadmin`)
  - Known limitations: 15-min token lifetime, no single sign-out v1

- [ ] **Step 2: Commit.**

```bash
git -c user.signingkey=/home/ankit/.ssh/id_ed25519_gh -c gpg.format=ssh \
    commit -S -m "docs(auth): operator runbook for AUTH_PROVIDER=external"
```

---

## Final Review

After Phase E lands:

- [ ] **Run the full Go test suite:** `cd /storage/intern/hooman/work/inferia-auth && go test ./...`. Expected: all green.
- [ ] **Run the full Python test suite:** `cd /storage/intern/hooman/work/InferiaLLM && make test`. Expected: all green, including new `test_jwks_verifier.py`, `test_oauth_router.py`, `test_middleware_external.py`.
- [ ] **Run the UI tests:** `cd inferia-auth-ui && npm test`. Expected: all green.
- [ ] **Run the smoke:** `bash scripts/sso_smoke.sh`. Expected: `ALL GOOD`.
- [ ] **Code coverage check** for new files ≥95%: `cd InferiaLLM && pytest --cov=inferia.services.api_gateway.rbac.jwks_verifier --cov=inferia.services.api_gateway.rbac.oauth_router --cov=inferia.services.api_gateway.rbac.oauth_client --cov-report=term-missing --cov-fail-under=95`. Same for `inferia-auth` via `go test -cover ./internal/usecase/oauth/...`.

If any of those fail, fix the offending task before declaring done.

---

## Self-Review Notes

Plan covers all spec sections (§1-§14). Cross-checks:

- **§2 in-scope items:** All five major workstreams have phases (A=OAuth backend, B=UI, C=gateway, D=dashboard, E=compose+smoke). ✓
- **§4 brand:** Phase B Task B1 (tokens), B2 (components), B4 (banned-word lint). ✓
- **§5 schema:** Task A1 (4 migrations). ✓
- **§6 FGA model:** Task A2 (model + seed + expand). ✓
- **§7 endpoints:** Tasks A6 (authorize), A7 (token), A8 (userinfo, revoke, discovery), A10 (session cookie). ✓
- **§8 UI structure:** Tasks B1–B5. ✓
- **§9 gateway changes:** Tasks C1–C6 (config, JWKS, oauth client, oauth router, middleware, login gate). ✓
- **§10 compose:** Task E1. ✓
- **§11 testing:** Each implementation task is TDD-first; smoke at E2. ✓
- **§12 failure modes:** Documented in E3 runbook. ✓
- **§13 migration:** Documented in E3. ✓
- **§14 security:** PKCE constant-time compare (A3), redirect_uri exact match (A6), state required (A6), token length caps (A3/A7/C2/C3/C4), token never logged (handlers use OAuth standard response shape, no raw token in logs). ✓

Type consistency: `IssueAccessTokenArgs.Audience` (A5) ↔ `JWKSVerifier(audience=...)` (C2) ↔ `app_namespace` config (C1) — all the same string `"inferiallm"`. `Scope` claim is space-separated string. `permissions` claim is `[]string`. Consistent across A5/A6/A7/C2/C5.

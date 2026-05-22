# inferia-auth SSO Integration Spec

> **Author:** Ankit Prasad · **Date:** 2026-05-23 · **Repos affected:** `InferiaLLM`, `inferia-auth`

## 1. Goal

Make `inferia-auth` the canonical authentication and authorization service for the Inferia ecosystem. InferiaLLM stops storing passwords (except for the local superadmin escape hatch) and instead trusts OAuth2 access tokens issued by inferia-auth, whose authorization data comes from inferia-auth's embedded OpenFGA model.

A single `.env` flag (`AUTH_PROVIDER=external`) turns this on. With it set, the dashboard logs in through inferia-auth via **Authorization Code + PKCE**, and every protected request to InferiaLLM is verified locally against inferia-auth's published JWKS — no per-request network calls to inferia-auth.

inferia-auth stays a generic SSO microservice. Future apps (inferia-worker UI, inferia-gate, ...) get their own `client_id` and app namespace and reuse the same login session.

## 2. Scope

**In scope (v1):**
- OAuth2 Authorization Code + PKCE endpoints on inferia-auth (`/oauth/authorize`, `/oauth/token`, `/oauth/userinfo`, `/oauth/revoke`, `/.well-known/openid-configuration`)
- OAuth client registry with per-client app namespace
- FGA model extension to expose namespaced permissions per role
- JWT access tokens carrying `roles`, `permissions`, `aud`, `org_id` claims
- New branded React UI (`inferia-auth-ui`) for login / logout / error pages
- InferiaLLM gateway changes: `/auth/callback` handler, JWKS-based middleware, claim-driven `UserContext`
- InferiaLLM dashboard: redirect-based login flow
- Local superadmin escape hatch (unchanged from today)
- docker-compose topology that runs all three services behind a single hostname for cookie sharing
- ≥95% unit test coverage on new code, integration tests for the full happy + 6 failure paths
- End-to-end smoke test: `docker compose up`, click through login, hit a protected endpoint

**Out of scope (deferred):**
- Single sign-out across apps (each app's access token simply expires within its TTL)
- Social login (Google/GitHub) UI — backend hooks exist in inferia-auth but UI not wired
- Passkeys / WebAuthn
- Consent screen for 3rd-party clients — internal clients auto-grant
- TOTP/2FA migration from InferiaLLM-local to inferia-auth — keep TOTP local for superadmin only
- Token introspection-based revocation — relies on 15-min access-token TTL for now
- Migration tool to copy existing InferiaLLM users into inferia-auth (operators handle out-of-band)

## 3. Architecture

```
                    ┌──────────────────────────────────────┐
                    │     Browser (Dashboard SPA)          │
                    │  PKCE: code_verifier+code_challenge  │
                    └──────┬───────────────────────┬───────┘
                           │ 1) GET /oauth/authorize?...
                           │                       ▲ 6) callback?code=...
                           ▼                       │
       ┌──────────────────────────────────┐        │
       │       inferia-auth (Go)          │        │
       │  /oauth/authorize ───┐           │        │
       │  /oauth/token        │ session   │        │
       │  /oauth/userinfo     │ cookie    │        │
       │  /oauth/revoke       │           │        │
       │  /.well-known/...    │           │        │
       │   ┌───────────────┐  │           │        │
       │   │ OpenFGA       │◀─┘           │        │
       │   │ (ReBAC)       │              │        │
       │   └───────────────┘              │        │
       │   ┌───────────────┐              │        │
       │   │ Postgres      │              │        │
       │   │ users/clients │              │        │
       │   │ /codes/tokens │              │        │
       │   └───────────────┘              │        │
       └────┬─────────────────────────────┘        │
            │ 3) 302 → /login?return_to=...        │
            ▼ 5) 302 back to /oauth/authorize      │
       ┌──────────────────────────────────┐        │
       │   inferia-auth-ui (React/Vite)   │        │
       │   /login  /error  /logout        │        │
       │   Branded per Inferia tokens     │        │
       └──────────────────────────────────┘        │
                                                   │
                           ┌───────────────────────┴──────────────┐
                           │     InferiaLLM API Gateway           │
                           │  /auth/callback (code→tokens)        │
                           │  /auth/login    (superadmin only)    │
                           │  middleware: JWKS verify             │
                           │  middleware: read claims → UserCtx   │
                           │  /.../* protected routes             │
                           └──────────────────────────────────────┘
```

**Service boundaries:**
- inferia-auth owns: user identity, password storage, OAuth code lifecycle, refresh tokens, OpenFGA model, role assignments, JWKS keys, login UI hosting.
- InferiaLLM owns: dashboard SPA, gateway proxying, business logic, shadow user records (joined by email), audit logs, superadmin local login.
- inferia-auth-ui owns: pixel-level branding, login form, error pages — knows nothing about InferiaLLM specifically.

## 4. Branding (Inferia Design System)

The spec for inferia-auth-ui follows the Inferia global design system. Tokens captured below — full reference lives at `docs/specs/inferia-brand-spec.md` (created alongside this spec).

### 4.1 Design tokens (CSS variables)

```css
:root {
  /* Light theme (default) */
  --bg-primary: #FAFAF8;
  --bg-secondary: #F2F0EC;
  --text-primary: #1A1A1A;
  --text-secondary: #5C5C5C;
  --text-muted: #9A9A9A;
  --accent-warm: #E8603C;
  --accent-soft: #F0E6D3;
  --border: #E5E2DC;
  --success: #3D7A5F;
  --white: #FFFFFF;
  --shadow-card: 0 1px 3px rgba(0, 0, 0, 0.06);
  --radius-card: 8px;
  --radius-control: 4px;
  --font-sans: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
  --font-mono: 'JetBrains Mono', ui-monospace, monospace;
}

[data-theme="dark"] {
  --bg-primary: #0a0a0a;
  --bg-secondary: #141414;
  --bg-tertiary: #1A1A1A;
  --text-primary: #E8E8E8;
  --text-secondary: #A0A0A0;
  --text-muted: #666666;
  --accent-warm: #E8603C;
  --border: #2A2A2A;
}
```

### 4.2 Component rules (enforced via Tailwind preset)

- **Buttons primary:** `bg-[--accent-warm] text-white rounded-[4px] px-6 py-3 text-sm font-medium`. Hover: slight darken via `hover:opacity-90`. No glow, no scale.
- **Buttons secondary:** transparent bg, `border border-[--border] text-[--text-primary] rounded-[4px]`.
- **Buttons ghost:** no border, `hover:underline`.
- **Inputs:** `bg-[--bg-secondary] border border-[--border] rounded-[4px] px-4 py-3 text-base focus:border-[--accent-warm] outline-none`. 16px font on mobile.
- **Cards:** `bg-[--bg-secondary] border border-[--border] rounded-[8px] shadow-[--shadow-card] p-8`.
- **Headlines:** `font-sans font-bold` (Inter 700/800). Body: Inter 400/500. Code: JetBrains Mono.
- **Spacing:** all values are multiples of 8 (`p-2 p-4 p-6 p-8 p-12 p-16` map to 8/16/24/32/48/64px).
- **Max content width:** 1200px; login card centered, max-width 480px.
- **Mobile-first responsive breakpoints:** 640, 1024, 1440px.
- **Logo:** inline SVG. Light bg → `#1A1A1A`. Dark bg → `#FFFFFF`. Used at top-left of every page, with at least half-icon-width clear space. Icon mark only on login card (no full lockup). Watermark variant (8% opacity) lives behind the form on desktop.
- **Motion:** gentle 200-300ms fade-in on mount; hover transitions 150ms ease-out; nothing else animates.

### 4.3 Copy rules

- No hyphens. "self hosted" not "self-hosted", "open weight" not "open-weight", "real time" not "real-time".
- Direct, warm, technical. No marketing speak.
- Example: button reads `Sign in` not `Login`. Error reads `That email and password don't match. Try again.` not `Authentication failed: invalid credentials`.
- Banned words anywhere in UI: revolutionizing, leveraging, transforming, journey.

### 4.4 Pages in v1

- `/login` — single card, email + password, "Sign in" button, link to `/forgot-password` (route exists, points to a "Contact your admin" stub for v1)
- `/error` — generic OAuth error display (invalid_client, access_denied, etc.) with friendly copy
- `/logout` — short confirmation, "You're signed out" + link back to the origin app
- `/forgot-password` — v1 stub: "Password resets are managed by your administrator. Reach out to them."

## 5. Data model changes

### 5.1 inferia-auth — new tables

```sql
-- oauth_clients: registered relying parties
CREATE TABLE oauth_clients (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  client_id     TEXT NOT NULL UNIQUE,
  client_name   TEXT NOT NULL,
  app_namespace TEXT NOT NULL,  -- 'inferiallm', 'inferia-worker', ...
  client_type   TEXT NOT NULL CHECK (client_type IN ('public', 'confidential')),
  client_secret_hash TEXT,      -- argon2id; NULL when client_type='public'
  redirect_uris TEXT[] NOT NULL,
  allowed_scopes TEXT[] NOT NULL DEFAULT ARRAY['openid','profile','email'],
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- oauth_codes: short-lived authorization codes
CREATE TABLE oauth_codes (
  code           TEXT PRIMARY KEY,            -- random 32 bytes, base64url
  client_id      TEXT NOT NULL REFERENCES oauth_clients(client_id),
  user_id        UUID NOT NULL,               -- references users.id
  redirect_uri   TEXT NOT NULL,
  scopes         TEXT[] NOT NULL,
  code_challenge TEXT NOT NULL,
  code_challenge_method TEXT NOT NULL CHECK (code_challenge_method IN ('S256')),
  expires_at     TIMESTAMPTZ NOT NULL,        -- now() + 60s
  used_at        TIMESTAMPTZ,                 -- single-use enforcement
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX oauth_codes_expires_at ON oauth_codes(expires_at);

-- oauth_refresh_tokens: opaque refresh tokens (rotation)
CREATE TABLE oauth_refresh_tokens (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  token_hash    TEXT NOT NULL UNIQUE,         -- sha256 of token
  client_id     TEXT NOT NULL REFERENCES oauth_clients(client_id),
  user_id       UUID NOT NULL,
  scopes        TEXT[] NOT NULL,
  expires_at    TIMESTAMPTZ NOT NULL,         -- now() + 168h (7 days)
  revoked_at    TIMESTAMPTZ,
  parent_id     UUID REFERENCES oauth_refresh_tokens(id),  -- rotation chain
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX oauth_refresh_tokens_user_id ON oauth_refresh_tokens(user_id);

-- oauth_sessions: SSO session cookies (server-side state)
CREATE TABLE oauth_sessions (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  session_key   TEXT NOT NULL UNIQUE,         -- random 32 bytes, base64url; stored hashed
  user_id       UUID NOT NULL,
  expires_at    TIMESTAMPTZ NOT NULL,         -- now() + 12h
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

Migrations go under `inferia-auth/migrations/` using the existing golang-migrate setup.

### 5.2 InferiaLLM — schema unchanged

No schema changes required. Shadow-user creation logic continues to use the existing `users` and `user_organizations` tables. Role and Permission tables become inactive (but not dropped) under external mode; they still drive the superadmin path.

## 6. FGA model extension

The existing model at `inferia-auth/openfga/model.fga` has `user`, `organization`, `role`. We add:

```fga
type app
  relations
    define permission_set: [permission_set]

type permission_set
  relations
    define defines_permission: [permission]
    define granted_to_role: [role]

type permission
  relations
    define included_in: [permission_set]
```

**Seed data (idempotent at boot):**
```
app:inferiallm     permission_set: ps:inferiallm-admin
app:inferiallm     permission_set: ps:inferiallm-member

ps:inferiallm-admin defines_permission permission:inferiallm:deployment:read
ps:inferiallm-admin defines_permission permission:inferiallm:deployment:write
ps:inferiallm-admin defines_permission permission:inferiallm:audit:read
... (full list mirrors current PermissionEnum)

ps:inferiallm-admin  granted_to_role role:admin
ps:inferiallm-member granted_to_role role:member
```

**Scope expansion at login time:**
```go
// pseudocode in inferia-auth
func (s *Service) ExpandPermissions(ctx, userID, appNamespace) []string {
    roles := fga.ListSubjectRoles(userID)                      // [admin]
    perms := fga.ListObjects("permission",
                             "included_in",
                             permission_sets_for(appNamespace, roles))
    return perms                                               // [inferiallm:deployment:read, ...]
}
```

Each app's permission catalog is seeded by that app on boot via a `POST /api/v1/apps/register` admin endpoint (also new — see §7.4) so adding a new app is purely additive.

## 7. inferia-auth backend changes

### 7.1 New REST handlers (Fiber, under `/oauth`)

| Handler | Path | Files |
|---|---|---|
| `OAuthAuthorize` | `GET /oauth/authorize` | `internal/transport/rest/handler/oauth_authorize.go` |
| `OAuthToken` | `POST /oauth/token` | `internal/transport/rest/handler/oauth_token.go` |
| `OAuthUserinfo` | `GET /oauth/userinfo` | `internal/transport/rest/handler/oauth_userinfo.go` |
| `OAuthRevoke` | `POST /oauth/revoke` | `internal/transport/rest/handler/oauth_revoke.go` |
| `OpenIDConfig` | `GET /.well-known/openid-configuration` | `internal/transport/rest/handler/oidc_discovery.go` |

### 7.2 New usecase / domain layer

- `internal/usecase/oauth/authorize.go` — checks client, validates redirect_uri exact match, validates scopes, checks SSO session, issues code
- `internal/usecase/oauth/token_exchange.go` — code→token exchange (verifies PKCE), refresh-token rotation
- `internal/usecase/oauth/scopes.go` — calls FGA to expand `(user, app_namespace) → []permission`
- `internal/domain/oauth/code.go`, `refresh.go`, `client.go`, `session.go` — entity types
- `internal/infrastructure/repository/oauth_*.go` — Postgres adapters

### 7.3 JWT claim shape (Ed25519, alg `EdDSA`)

```json
{
  "iss": "https://auth.inferia.local",
  "sub": "user:01HX...",
  "aud": "inferiallm",
  "exp": 1716480000,
  "iat": 1716479100,
  "type": "access",
  "email": "ankit@example.com",
  "org_id": "01HW...",
  "org_ids": ["01HW..."],
  "roles": ["admin"],
  "permissions": [
    "inferiallm:deployment:read",
    "inferiallm:deployment:write",
    "inferiallm:audit:read"
  ],
  "scope": "openid profile email inferiallm"
}
```

Refresh tokens are opaque (random 32B base64url). They are stored hashed (`sha256`) — never logged.

### 7.4 Admin endpoints (internal, behind a static admin key)

- `POST /api/v1/apps/register` — `{namespace, permissions: [...]}` — idempotent app + permission_set seeding
- `POST /api/v1/clients` — register an OAuth client (used by ops, also seeded on boot via env var for `inferiallm-dashboard`)

These are protected by a separate `ADMIN_API_KEY` env var (matches existing `INTERNAL_API_KEY` convention in InferiaLLM).

### 7.5 SSO session cookie

Cookie name: `inferia_auth_session`. Attributes: `HttpOnly; Secure; SameSite=Lax; Path=/; Domain=<inferia-auth-domain>; Max-Age=43200` (12 hours). Value is the opaque `session_key` from `oauth_sessions`. The cookie binds the browser to a logged-in user; `/oauth/authorize` reads it to skip the login form on subsequent app launches.

## 8. inferia-auth-ui (new React app)

### 8.1 Layout

- New top-level directory `inferia-auth-ui/` in the `inferia-auth` repo (sibling to `cmd/`, `internal/`, `pkg/`).
- Stack: Vite + React 19 + TypeScript + TailwindCSS + Shadcn (matches InferiaLLM dashboard, so engineers move freely between the two).
- Build output goes to `inferia-auth-ui/dist/`. The Go service serves the built bundle from `/ui/*` via `fiber.Static`. (No separate web server in v1 — keeps the deployment unit single.)

### 8.2 File structure

```
inferia-auth-ui/
  index.html
  vite.config.ts
  tailwind.config.ts             # imports brand preset
  src/
    main.tsx
    App.tsx                      # routes
    styles/
      tokens.css                 # CSS variables from §4.1
      tailwind-preset.ts         # brand colors / spacing exported as preset
    routes/
      Login.tsx
      Error.tsx
      Logout.tsx
      ForgotPassword.tsx
    components/
      Logo.tsx                   # inline SVG icon mark
      Watermark.tsx              # 8% opacity background
      Button.tsx                 # primary/secondary/ghost variants
      Input.tsx                  # 4px radius, focus ember
      Card.tsx                   # 8px radius, shadow
      ThemeToggle.tsx            # localStorage-backed light/dark
    lib/
      api.ts                     # POST /api/v1/auth/login wrapper
      queryParams.ts             # parse return_to safely
  public/
    inferia-icon.svg             # brain helmet icon mark (color variants)
    favicon.ico
```

### 8.3 Wire integration with the Go service

- Fiber app gets a new static mount: `app.Static("/ui", "./inferia-auth-ui/dist")` plus a SPA fallback handler so client routing works.
- `/login` (top-level path) redirects to `/ui/login` while preserving query string.
- On submit, `Login.tsx` POSTs to `/api/v1/auth/login` (existing endpoint), receives session cookie (Set-Cookie on the response), then `window.location = return_to_url`.
- All routes read `?return_to=` and validate it's a same-origin URL before redirecting.

### 8.4 Branding self-check

After UI build, a small Node script (`scripts/brand-check.ts`) runs as part of `npm run build`:
- Greps for banned words (revolutionizing, leveraging, transforming, journey)
- Greps for hyphenated compounds in user-facing copy (`self-hosted`, `open-weight`, `real-time`)
- Fails the build on violation
This makes the brand rules enforced, not aspirational.

## 9. InferiaLLM changes

### 9.1 Gateway middleware (`api_gateway/rbac/middleware.py`)

Replace the introspect-based `_resolve_external_token` with a JWKS-based local verify:

```python
# new file: api_gateway/rbac/jwks_verifier.py
class JWKSVerifier:
    def __init__(self, jwks_url, cache_ttl=3600): ...
    async def verify(self, token: str) -> dict:
        """Verify Ed25519 JWT against cached JWKS. Returns claims dict."""
```

The verifier:
- Fetches `/.well-known/jwks.json` once an hour (default), holds the keyset in memory.
- Validates `alg=EdDSA`, `iss==settings.external_auth_issuer`, `aud==settings.app_namespace` ("inferiallm"), `exp > now`, `type=="access"`.
- Returns the claims dict on success, raises on any mismatch.

`_resolve_external_token` becomes:

```python
async def _resolve_external_token(db, token: str) -> UserContext:
    claims = await jwks_verifier.verify(token)
    email = claims["email"]
    external_user_id = claims["sub"].split(":", 1)[1]  # strip "user:" prefix
    user, _, _ = await get_or_create_shadow_user(db, email=email, external_id=external_user_id)
    return UserContext(
        user_id=user.id,
        username=user.email,
        email=user.email,
        roles=claims.get("roles", []),
        permissions=claims.get("permissions", []),
        org_id=claims.get("org_id"),
        quota_limit=10000,
        quota_used=0,
    )
```

The shadow-user lookup ignores any local role assignment — roles and permissions come straight from the JWT.

### 9.2 New gateway endpoint: `/auth/callback`

```python
@router.get("/auth/callback")
async def oauth_callback(code: str, state: str, http_request: Request, db: AsyncSession = Depends(get_db)):
    """
    Receives ?code from inferia-auth, exchanges for tokens, redirects to dashboard.
    """
    # Verify state against the cookie set during /auth/start
    cookie_state = http_request.cookies.get("oauth_state")
    if not cookie_state or cookie_state != state:
        raise HTTPException(400, "Invalid state")

    verifier_cookie = http_request.cookies.get("oauth_verifier")
    if not verifier_cookie:
        raise HTTPException(400, "Missing verifier")

    # Exchange code → tokens
    tokens = await exchange_code(code, verifier_cookie)

    # Set refresh in httpOnly cookie scoped to gateway origin
    response = RedirectResponse(url="/")
    response.set_cookie(
        "refresh_token",
        tokens["refresh_token"],
        httponly=True, secure=True, samesite="lax",
        max_age=604800,
    )
    # Pass access token to dashboard via short-lived URL fragment (#access_token=...)
    response.headers["Location"] = f"/#access_token={tokens['access_token']}"
    return response
```

And a corresponding `/auth/start`:

```python
@router.get("/auth/start")
async def oauth_start():
    """Initiate OAuth: generate PKCE pair, redirect to inferia-auth /oauth/authorize."""
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(32)
    url = f"{settings.external_auth_url}/oauth/authorize?response_type=code" \
          f"&client_id={settings.oauth_client_id}&redirect_uri={settings.oauth_redirect_uri}" \
          f"&scope=openid+profile+email+{settings.app_namespace}&state={state}" \
          f"&code_challenge={challenge}&code_challenge_method=S256"
    response = RedirectResponse(url=url)
    # Stash verifier+state in short-lived httpOnly cookies
    response.set_cookie("oauth_state", state, httponly=True, secure=True, samesite="lax", max_age=600)
    response.set_cookie("oauth_verifier", verifier, httponly=True, secure=True, samesite="lax", max_age=600)
    return response
```

### 9.3 Superadmin endpoint stays

`POST /auth/login` keeps working — but only successfully — for the superadmin (`SUPERADMIN_EMAIL`). For any other user under `AUTH_PROVIDER=external`, it returns 403 with `Use /auth/start to sign in` and a link.

Code:
```python
@router.post("/login", response_model=AuthToken)
async def login(request: LoginRequest, http_request: Request, db: AsyncSession = Depends(get_db)):
    use_external = settings.auth_provider == "external" and settings.external_auth_url
    if use_external and request.username != settings.superadmin_email:
        raise HTTPException(403, "Direct password login is disabled. Use /auth/start.")
    # Otherwise, fall through to existing local logic (superadmin path or local mode).
    ...
```

### 9.4 Dashboard changes

- `apps/dashboard/src/services/authService.ts`: Replace direct `POST /auth/login` with `window.location = "/auth/start"`. On dashboard mount, parse `#access_token=` from URL fragment, stash in `tokenStore`, then `history.replaceState(null, "", "/")` to clear the fragment.
- Add a "Sign in with Inferia" button on the dashboard's existing `/login` page that triggers the redirect flow. The legacy username/password form remains visible **only when `VITE_AUTH_PROVIDER=local`**, controlled by a Vite-time env var that gets baked into the build (read from the same source as the gateway's `AUTH_PROVIDER`).
- Logout: `POST /auth/logout` calls `/oauth/revoke` against inferia-auth and clears local refresh cookie, then redirects to `inferia-auth/logout`.

### 9.5 Config changes

`api_gateway/config.py`:
```python
auth_provider: Literal["local", "external"] = "local"
external_auth_url: Optional[str] = None
external_auth_issuer: Optional[str] = None              # NEW: 'iss' claim expected
app_namespace: str = "inferiallm"                       # NEW: 'aud' claim expected
oauth_client_id: Optional[str] = None                   # NEW
oauth_redirect_uri: Optional[str] = None                # NEW: e.g. 'https://gw.example/auth/callback'
oauth_jwks_cache_ttl_seconds: int = 3600                # NEW
```

`.env.sample`:
```bash
# Auth: when external, login flows through inferia-auth (Auth Code + PKCE).
AUTH_PROVIDER="local"                                   # local | external
EXTERNAL_AUTH_URL="https://auth.inferia.local"
EXTERNAL_AUTH_ISSUER="https://auth.inferia.local"
APP_NAMESPACE="inferiallm"
OAUTH_CLIENT_ID="inferiallm-dashboard"
OAUTH_REDIRECT_URI="https://inferiallm.local/auth/callback"
```

Validation: when `AUTH_PROVIDER=external`, all five EXTERNAL_/OAUTH_* fields must be set or the gateway refuses to boot.

## 10. Docker compose topology

`docker-compose.sso.yml` (new file; opts in by `docker compose -f docker-compose.unified.yml -f docker-compose.sso.yml up`):

```yaml
services:
  caddy:
    image: caddy:2
    ports:
      - "443:443"
    volumes:
      - ./deploy/Caddyfile:/etc/caddy/Caddyfile:ro
      - caddy_data:/data
    depends_on: [inferia-auth, inferia-app]

  inferia-auth:
    image: inferia-auth:dev
    build: ../inferia-auth
    environment:
      DATABASE_URL: postgres://postgres:postgres@postgres:5432/inferia_auth?sslmode=disable
      OPENFGA_DATABASE_URL: postgres://postgres:postgres@postgres:5432/openfga?sslmode=disable
      JWT_PRIVATE_KEY: ${INFERIA_AUTH_JWT_PRIVATE_KEY}
      JWT_PUBLIC_KEY: ${INFERIA_AUTH_JWT_PUBLIC_KEY}
      ADMIN_API_KEY: ${INFERIA_AUTH_ADMIN_API_KEY}
      OAUTH_SEED_CLIENT_ID: "inferiallm-dashboard"
      OAUTH_SEED_REDIRECT_URI: "https://inferia.local/auth/callback"
    depends_on: [postgres]

  inferia-app:
    extends:
      file: docker-compose.unified.yml
      service: inferia-app
    environment:
      AUTH_PROVIDER: "external"
      EXTERNAL_AUTH_URL: "https://auth.inferia.local"
      EXTERNAL_AUTH_ISSUER: "https://auth.inferia.local"
      APP_NAMESPACE: "inferiallm"
      OAUTH_CLIENT_ID: "inferiallm-dashboard"
      OAUTH_REDIRECT_URI: "https://inferia.local/auth/callback"
```

Caddyfile:
```
inferia.local {
  reverse_proxy /auth/* inferia-app:8000
  reverse_proxy /api/*  inferia-app:8000
  reverse_proxy /       inferia-app:8000
}

auth.inferia.local {
  reverse_proxy inferia-auth:3000
}
```

Both hostnames resolve to 127.0.0.1 via `/etc/hosts` entries the docs ask the operator to add for local dev. This keeps cookies on `auth.inferia.local` separate from `inferia.local` — exactly the production posture.

## 11. Testing strategy

### 11.1 Unit coverage (≥95% per global rule)

**inferia-auth (Go):**
- PKCE verifier: S256 happy path, mismatched challenge, malformed challenge, wrong method (`plain` rejected), code expired, code reused (single-use enforcement)
- Code issuance: scope filter (clients can't request scopes they don't have), redirect_uri exact match (not prefix match), state round-tripped intact
- Token endpoint: missing grant_type, unsupported grant_type, expired refresh, revoked refresh, rotated refresh (parent_id chain)
- Scope expansion: user with no roles, user with multiple roles, app namespace not registered, permission set granted to multiple roles
- JWKS publication: key rotation simulation, cache headers correct
- **Edge: input length overflow** — code_challenge max 128 chars, redirect_uri max 2048 chars, scope max 4096 chars; reject with 400 not crash

**InferiaLLM gateway (Python):**
- JWKSVerifier: valid token, expired token, wrong iss, wrong aud, missing claims (`roles`, `permissions`), wrong alg, signature tampered, JWKS cache miss + refetch, JWKS endpoint unreachable
- Callback handler: missing state, mismatched state, missing verifier cookie, token exchange 4xx response, token exchange network error
- /auth/start: state and verifier cookies set with correct attributes
- Middleware integration: local superadmin still works when `AUTH_PROVIDER=external`, external token round-trips, malformed token → 401 with body, no token → 401, OPTIONS request bypasses auth
- **Edge: input length overflow** — username/password lengths capped (255/1024); refresh_token cookie max 4096 chars

**inferia-auth-ui (Vitest + Testing Library):**
- Login form: submits credentials, shows error on 401, redirects on success
- Brand-check script: passes on clean files, fails when banned words / hyphens appear
- Theme toggle: persists to localStorage, applies `data-theme` attribute
- Accessibility: tab order on login form, ARIA labels on inputs, focus visible

### 11.2 Integration tests

**inferia-auth:** docker-compose with Postgres + the service; pytest (`auth_e2e_test.go` if Go test, or Python harness) walks:
1. Seed client + app + permission_set
2. POST /api/v1/auth/login → session cookie
3. GET /oauth/authorize → 302 to /oauth/callback with code
4. POST /oauth/token → JWT decoded, claims asserted
5. POST /oauth/token grant_type=refresh_token → new JWT with same identity, different jti
6. POST /oauth/revoke → subsequent refresh fails

**InferiaLLM:** existing pytest harness, new file `tests/test_sso_integration.py` using a fake JWKS HTTP server + a self-signed Ed25519 keypair. Walks the full middleware path + callback handler against forged-but-valid tokens.

### 11.3 End-to-end smoke (`docker compose up` test, as user requested)

A bash script `scripts/sso_smoke.sh`:
1. `docker compose -f docker-compose.unified.yml -f docker-compose.sso.yml up -d --build`
2. Wait for `https://inferia.local/health` and `https://auth.inferia.local/health` to return 200
3. `curl` /auth/start, follow the redirect, drive the login form via a Playwright headless script (`scripts/sso_smoke.spec.ts`) — log in with seeded test user, land back at dashboard, hit a protected endpoint
4. Assert response body has the expected user identity
5. Tear down

This is the test the user explicitly asked for ("docker compose up and test").

## 12. Failure modes & operational notes

- **inferia-auth unreachable at gateway boot:** JWKS fetch fails. Gateway logs `EXTERNAL_AUTH_URL unreachable` and starts in degraded mode — only superadmin can log in until JWKS recovers.
- **inferia-auth unreachable mid-session:** existing tokens keep working until expiry (15 min) since verification is local. New logins fail; users see "Sign in service unavailable" with a retry link.
- **Clock skew:** JWKSVerifier tolerates 60s skew on `exp` and `iat`.
- **Refresh token compromise:** rotation chain detected (re-use of a revoked refresh token) → invalidate entire chain, force re-login.
- **Superadmin lockout recovery:** local DB password reset CLI (`inferiallm reset-superadmin`) remains; documented in `docs/operations/auth.md` (new).
- **CORS:** inferia-auth allows the configured `oauth_redirect_uri` origins only. No wildcard.
- **Rate limiting:** `/oauth/token` and `/oauth/authorize` get the same rate limiter as `/auth/login` (existing in inferia-auth: 60 req/min/IP).

## 13. Migration / rollout

1. Ship inferia-auth changes (OAuth endpoints, FGA model extension, oauth_clients seeded with InferiaLLM dashboard) — backward compatible, no existing endpoint changes.
2. Build & deploy inferia-auth-ui co-mounted at `/ui/*`.
3. Ship InferiaLLM gateway with `/auth/callback`, `/auth/start`, new JWKS verifier — `AUTH_PROVIDER=local` still works, no behavior change.
4. Ship dashboard with "Sign in with Inferia" button — appears only when build env says external.
5. Operator flips `.env`: `AUTH_PROVIDER=external` + all OAUTH_/EXTERNAL_AUTH_* fields. Restart gateway. Local non-superadmin logins start refusing; users redirect through inferia-auth.
6. Existing local InferiaLLM passwords become inert (rows kept for audit-trail consistency). Operators provision users in inferia-auth out of band (re-uses existing inferia-auth admin tools).
7. Roll back: flip flag to `local`, restart. Local users start working again. inferia-auth tokens stop being accepted within 15 min.

## 14. Security considerations

- **PKCE mandatory for all public clients** — server rejects authorization-code grants without `code_verifier` for public clients.
- **redirect_uri exact-string match** — not prefix, not wildcard. The OAuth spec allows looser matching; we choose tighter.
- **State parameter required** — server rejects authorize requests without `state`. Mitigates CSRF on the redirect leg.
- **Tokens never logged** — both access tokens and refresh tokens are scrubbed from request/response logs in inferia-auth (Fiber middleware) and InferiaLLM gateway (`logging.Filter`).
- **JWKS endpoint not gated** — public per spec, fine.
- **Refresh token rotation** — every refresh issues a new token and invalidates the previous one; reuse is treated as compromise.
- **No client_secret over the wire for public clients** — only PKCE.
- **Session fixation defense** — session cookie rotated on every successful login.
- **Password storage** stays as today (Argon2id via inferia-auth) — InferiaLLM's bcrypt hashes are no longer used in external mode.

---

## Appendix A: Banned vs preferred copy

| Banned | Preferred |
|---|---|
| Authentication failed | That email and password don't match |
| Invalid credentials | We don't recognize this account |
| Authorization required | You need to sign in first |
| Authorize | Sign in |
| Login | Sign in |
| Logout | Sign out |
| Real-time updates | Real time updates |
| Self-hosted | Self hosted |
| Leveraging FGA | Backed by FGA |

## Appendix B: Brand spec source

Full Inferia design system, including all tokens, component rules, copy rules, and logo usage, is mirrored at `docs/specs/inferia-brand-spec.md`. That file is the source of truth referenced by every Inferia product UI.

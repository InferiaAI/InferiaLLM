# Authentication Operations Guide

This guide covers the operational details of InferiaLLM's authentication
modes: switching between them, seeding users, rotating keys, recovering from
lockouts, and the known limitations of v1.

---

## Modes

InferiaLLM supports two authentication modes, selected by the
`AUTH_PROVIDER` environment variable on the API gateway:

| Mode | What it does | When to use |
|------|--------------|-------------|
| `local` (default) | Passwords stored in InferiaLLM's `users` table. JWTs signed with `JWT_SECRET_KEY` (HS256). Login via `POST /auth/login`. | Single-tenant deployments, dev, or when no IdP is available. |
| `external` | OAuth2 Authorization Code + PKCE against inferia-auth. JWTs are Ed25519, verified locally via cached JWKS. Login via `/auth/start` → IdP → `/auth/callback`. | Multi-app SSO, sharing identity with other Inferia apps, or when an existing inferia-auth deployment is already in place. |

The local superadmin (`SUPERADMIN_EMAIL` / `SUPERADMIN_PASSWORD`) always
works regardless of mode — see [Superadmin recovery](#superadmin-recovery).

---

## Switching to external mode

### 1. Stand up inferia-auth

The bundled `make docker-up-sso` brings up a complete topology for local
development: postgres, redis, inferia-auth (with an embedded OpenFGA
authorizer), inferia-app, and a Caddy reverse proxy with self-signed certs.

```bash
make docker-up-sso
```

For production: deploy inferia-auth standalone (its own Dockerfile and
compose file live at `inferia-auth/`); point InferiaLLM at it via the env
vars below.

### 2. Add hostnames to `/etc/hosts` (local dev only)

The local-dev compose uses `tls internal` with hardcoded `.local`
hostnames. Browsers (and inferia-app's server-side HTTP calls to
inferia-auth) need these to resolve to 127.0.0.1:

```
127.0.0.1 inferia.local
127.0.0.1 auth.inferia.local
```

In production this is replaced by real DNS + a publicly trusted cert.

### 3. Set gateway env vars

In `.env` (or your container orchestrator's secret store):

```ini
AUTH_PROVIDER=external
EXTERNAL_AUTH_URL=https://auth.inferia.local           # base URL of inferia-auth
EXTERNAL_AUTH_ISSUER=https://auth.inferia.local        # expected `iss` claim
APP_NAMESPACE=inferiallm                                # expected `aud` claim
OAUTH_CLIENT_ID=inferiallm-dashboard                    # client_id in oauth_clients
OAUTH_REDIRECT_URI=https://inferia.local/auth/callback  # MUST be byte-identical to the value seeded in oauth_clients
OAUTH_JWKS_CACHE_TTL_SECONDS=3600
COOKIE_SECURE=true                                      # send cookies only over TLS

# Dashboard build-time vars (Vite inlines these into the SPA bundle):
VITE_AUTH_PROVIDER=external
VITE_EXTERNAL_AUTH_URL=https://auth.inferia.local
```

### 4. Restart the gateway and verify

```bash
docker compose restart inferia-app
curl -k https://inferia.local/health
# {"status":"ok"}
```

Watch the gateway logs on first request — you should see a successful JWKS
fetch from `https://auth.inferia.local/.well-known/jwks.json`. The cache
TTL is `OAUTH_JWKS_CACHE_TTL_SECONDS`; subsequent verifications hit cache.

### 5. Provision users

See [Seeding users in inferia-auth](#seeding-users-in-inferia-auth) below.

### 6. Effect on existing local users

Once `AUTH_PROVIDER=external` is set, non-superadmin local password
sign-in is rejected (HTTP 403 with `auth_provider_disabled`). The
superadmin path stays open. Existing local user rows are NOT migrated —
the user must exist in inferia-auth's `users` table for SSO to work for
them. See `cmd/server/seed.go` in inferia-auth for the on-boot seed
hooks, or use `/api/v1/auth/register` (see next section).

---

## Seeding users in inferia-auth

inferia-auth exposes a public registration endpoint at
`POST /api/v1/auth/register`. For the first admin, seed it directly:

```bash
curl -sk -X POST https://auth.inferia.local/api/v1/auth/register \
  -H 'Content-Type: application/json' \
  -d '{
    "email":"admin@example.com",
    "password":"choose-a-strong-password",
    "display_name":"Admin"
  }'
```

The user is created with the default role (`member`) in their default
organization. To grant `admin`, use inferia-auth's admin-only endpoints
under `/api/v1` (TODO: document the admin CLI once it lands; for now, an
operator with a superadmin JWT can call them directly via curl).

---

## OAuth client registration

The `inferiallm-dashboard` client is auto-seeded by inferia-auth on first
boot via `cmd/server/seed.go`. It uses these defaults, all overridable
via env on the inferia-auth process:

| Env var | Default | Description |
|---------|---------|-------------|
| `OAUTH_SEED_CLIENT_ID` | `inferiallm-dashboard` | OAuth2 `client_id` |
| `OAUTH_SEED_CLIENT_NAME` | `InferiaLLM Dashboard` | Human-readable name |
| `OAUTH_SEED_APP_NAMESPACE` | `inferiallm` | Used as the JWT `aud` claim |
| `OAUTH_SEED_REDIRECT_URI` | `http://localhost:8000/auth/callback` | Allowed redirect URI (CSV-separated) |
| `OAUTH_SEED_CLIENT_TYPE` | `public` | `public` (PKCE) or `confidential` |
| `OAUTH_SEED_ALLOWED_SCOPES` | `openid,profile,email,inferiallm` | Allowed `scope` values |
| `OAUTH_SEED_DISABLED` | `false` | Skip the on-boot seed entirely |

> **The redirect URI must match byte-for-byte** between this seed and the
> gateway's `OAUTH_REDIRECT_URI`. inferia-auth rejects authorize requests
> with mismatching `redirect_uri` (per RFC 6749 §3.1.2.4) and the gateway
> rejects callbacks with mismatched values.

To re-register the client (e.g. to add a second redirect URI), update the
env vars and restart inferia-auth; the seeder is idempotent.

---

## JWKS key rotation

Ed25519 signing keys live on inferia-auth in `JWT_PRIVATE_KEY` /
`JWT_PUBLIC_KEY` (base64-encoded raw bytes). To rotate without downtime:

1. Generate a new Ed25519 keypair. The .env.example in inferia-auth
   embeds a one-liner:
   ```bash
   python3 -c "from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey; from cryptography.hazmat.primitives import serialization; import base64; k=Ed25519PrivateKey.generate(); priv=k.private_bytes(serialization.Encoding.Raw,serialization.PrivateFormat.Raw,serialization.NoEncryption()); pub=k.public_key().public_bytes(serialization.Encoding.Raw,serialization.PublicFormat.Raw); print('JWT_PRIVATE_KEY='+base64.b64encode(priv+pub).decode()); print('JWT_PUBLIC_KEY='+base64.b64encode(pub).decode())"
   ```
2. Add the new public key to the JWKS list **alongside the old one** so
   the gateway accepts tokens signed by either key during the cache TTL
   window. (v1 ships a single-key JWKS; multi-key support is a TODO. For
   now, plan for a brief window of forced re-authentication during
   rotation.)
3. Wait at least `OAUTH_JWKS_CACHE_TTL_SECONDS` (default 1 hour) so all
   gateway caches refresh.
4. Switch the signer to the new key; remove the old one.

InferiaLLM's gateway picks up the new key on the next cache refresh. To
force an immediate refresh, restart the gateway process.

---

## Superadmin recovery

The `SUPERADMIN_EMAIL` / `SUPERADMIN_PASSWORD` user is provisioned by
InferiaLLM itself (not inferia-auth) and signed with the local
`JWT_SECRET_KEY` (HS256). The auth middleware tries local JWT decode
**first**; only on failure does it fall through to JWKS verification.
This means:

- The superadmin can always log in via `POST /auth/login` directly,
  even when `AUTH_PROVIDER=external` and inferia-auth is down.
- The superadmin's JWT is recognised by every InferiaLLM service in the
  cluster (since they share `JWT_SECRET_KEY`).

To reset the superadmin password from the host:

```bash
inferiallm reset-superadmin --email admin@example.com --password new-strong-password
```

(This CLI subcommand rewrites the row in the local `users` table
directly. It does NOT touch inferia-auth.)

---

## Cookie semantics

In external mode the gateway sets three cookies during the OAuth flow:

| Name | Lifetime | Purpose |
|------|----------|---------|
| `inferia_pkce_verifier` | 10 min | PKCE verifier echoed back on `/auth/callback` |
| `inferia_oauth_state` | 10 min | CSRF binding between `/auth/start` and `/auth/callback` |
| `inferia_refresh_token` | 7 days (configurable) | HttpOnly refresh token; never exposed to JS |

inferia-auth additionally sets a `sso_session` cookie scoped to its own
domain (`auth.inferia.local`); this is what enables "single sign-on" — a
user already authenticated against inferia-auth can authorize a new
client without re-typing their password.

All cookies use:
- `HttpOnly` (not readable from JS)
- `Secure` when `COOKIE_SECURE=true` (set this in production)
- `SameSite=Lax` (browsers send them on same-site top-level navigations,
  which is what we need for the OAuth redirect back to the gateway)

---

## Known limitations (v1)

- **Access tokens are stateless.** Revocation requires waiting for the
  15-minute TTL. To force-revoke a session, kill the refresh token via
  inferia-auth's `POST /oauth/revoke` — the user gets one more
  15-minute window of access from any cached access token, then is
  locked out.
- **No single sign-out across apps.** Each app's session expires
  independently. Clicking "Logout" on the dashboard logs out of
  InferiaLLM, then redirects to inferia-auth's `/logout` to clear the
  SSO session cookie, but does not propagate to other apps that may be
  using inferia-auth.
- **Password reset UI is a stub.** The `/forgot-password` route in
  inferia-auth-ui renders a placeholder; operators handle resets out of
  band (direct DB update or via the inferia-auth admin API).
- **2FA stays InferiaLLM-local.** Only the superadmin path enforces 2FA
  (via `inferiallm`'s TOTP table). The inferia-auth flow does not yet
  carry a 2FA step.
- **JWKS rotation requires a re-auth window.** The v1 JWKS document
  serves a single key. Until multi-key rotation lands, every key change
  forces clients to re-authenticate at the next access token refresh.
_The three Phase E blockers below were resolved in Phase F1
(inferia-auth commits `dbaea82`, `275899d`, `eb271c3` plus InferiaLLM
`417fd41`). Kept here as historical context — every item is now
exercised by the SSO smoke._

- ~~**FGA permission tree seeding has a known multi-colon-id bug.**~~
  Fixed: the seeder now writes permission ids in dotted form
  (`inferiallm.deployment.read`) at the FGA boundary while keeping
  the canonical colon form everywhere else.
  `OAUTH_SEED_DISABLED` defaults to `false` and the SQL seed-client
  workaround in `deploy/` has been removed.

- ~~**OAuth handlers and OIDC discovery are not wired in
  `inferia-auth/cmd/server/main.go`.**~~ Fixed: all five OAuth/OIDC
  endpoints are instantiated and passed to `rest.NewServer`. Verified
  by route-mount integration test + the SSO smoke.

- ~~**JWKS path is `/api/v1/.well-known/jwks.json`, not
  `/.well-known/jwks.json`.**~~ Fixed: JWKS is mounted at the root
  well-known path with a `/api/v1` alias for back-compat. The OIDC
  discovery document advertises the root path.

---

## Troubleshooting

### `JWKSError: failed to fetch JWKS`

The gateway can't reach `https://auth.inferia.local/.well-known/jwks.json`.
Possible causes:

- inferia-auth is down — check `docker compose ps`.
- The Caddy alias for `auth.inferia.local` on the SSO network is missing
  — verify `aliases:` on the caddy service in
  `deploy/docker-compose.sso.yml`.
- The gateway is using HTTP plain-text against a TLS endpoint — confirm
  `EXTERNAL_AUTH_URL` starts with `https://`.
- DNS resolution fails inside the container — try
  `docker compose exec inferia-app getent hosts auth.inferia.local`.

Only the superadmin can log in until this is resolved.

### `invalid_redirect_uri` on `/auth/callback`

inferia-auth rejects the authorize request because the gateway's
`OAUTH_REDIRECT_URI` doesn't match any entry in the `oauth_clients` row's
`redirect_uris[]` column. The match is byte-exact (no trailing-slash
normalisation, no scheme normalisation). Fix the gateway env or update
the row in inferia-auth.

### `aud claim mismatch`

The gateway's `APP_NAMESPACE` doesn't match the `aud` claim in the
issued token. Make sure `APP_NAMESPACE` (gateway) and
`OAUTH_SEED_APP_NAMESPACE` (inferia-auth) are both `inferiallm`.

### `JWT signature verification failed` after recent key rotation

The gateway has a stale JWKS cache. Wait
`OAUTH_JWKS_CACHE_TTL_SECONDS` seconds for the next fetch, or restart
the gateway to force an immediate refresh.

### Cookies not set / browser shows "missing state cookie"

Likely a `COOKIE_SECURE` / TLS mismatch. If the gateway is behind plain
HTTP (e.g. local dev without Caddy), set `COOKIE_SECURE=false`. In
production behind TLS the value must be `true` so browsers ship the
cookie back over HTTPS only.

### `dependency failed to start: container inferia-auth-sso is unhealthy`

Look at the inferia-auth logs:
```bash
docker compose -f deploy/docker-compose.sso.yml logs inferia-auth
```

Most common causes:
- Migrations haven't run yet — the compose's `inferia-auth-migrate`
  init container should have completed first.
- `JWT_PRIVATE_KEY` / `JWT_PUBLIC_KEY` are missing or malformed.
- The OAuth seed failed. Look for `oauth seed complete` in the logs;
  if absent, scan for the underlying postgres or OpenFGA error. Note
  that `OAUTH_SEED_DISABLED=true` skips the seed entirely — use it as
  a temporary workaround only.

### Smoke script: `ERROR: /etc/hosts is missing the SSO hostnames.`

Add the two `inferia.local` lines per
[step 2 above](#2-add-hostnames-to-etchosts-local-dev-only) and re-run.

---

## Running the SSO smoke

The end-to-end smoke at `scripts/sso_smoke.sh` brings up the full SSO
topology, seeds a test user, and drives the OAuth flow with Playwright.

Preconditions:
1. Docker + docker compose installed and runnable by the current user.
2. `/etc/hosts` contains the two `inferia.local` entries.
3. Ports 80 and 443 on the host are free.

Run:
```bash
bash scripts/sso_smoke.sh
```

On success: prints `ALL GOOD`. On failure: dumps the last 200 lines of
compose logs and exits non-zero. Use `KEEP_STACK_UP=1` to leave the
stack running for manual debugging.

### Curl-based smoke (when `/etc/hosts` is not writable)

If you cannot edit `/etc/hosts` (e.g. CI runners, container hosts), the
same end-to-end checks can be driven directly against the running
compose by using `curl --resolve` to spoof DNS:

```bash
make docker-up-sso

# 1. OIDC discovery + JWKS (proves F1a + F1b: handlers wired, JWKS at root)
curl -sk --resolve auth.inferia.local:443:127.0.0.1 \
  https://auth.inferia.local/.well-known/openid-configuration | jq .
curl -sk --resolve auth.inferia.local:443:127.0.0.1 \
  https://auth.inferia.local/.well-known/jwks.json | jq .keys[0]

# 2. Boot-time FGA seed succeeded (proves F1c: multi-colon-id fix)
docker logs inferia-auth-sso 2>&1 | grep "oauth seed complete"

# 3. Register a user + log in to get an SSO session cookie
curl -sk --resolve auth.inferia.local:443:127.0.0.1 \
  -X POST https://auth.inferia.local/api/v1/auth/register \
  -H 'Content-Type: application/json' \
  -d '{"email":"smoke@inferia.local","password":"smoke-password-1234","display_name":"Smoke"}'
curl -sk --resolve auth.inferia.local:443:127.0.0.1 \
  -X POST https://auth.inferia.local/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"smoke@inferia.local","password":"smoke-password-1234"}' \
  -c /tmp/sso.cookies

# 4. Drive /oauth/authorize → expect 302 to redirect_uri?code=...
CHALLENGE=$(echo -n verifier-1234567890abcdefghijklmnopqrstuvwxyzABCDEF \
  | openssl dgst -binary -sha256 | base64 | tr '+/' '-_' | tr -d '=')
curl -sk --resolve auth.inferia.local:443:127.0.0.1 -i \
  "https://auth.inferia.local/oauth/authorize?response_type=code\
&client_id=inferiallm-dashboard\
&redirect_uri=https://inferia.local/auth/callback\
&scope=openid&state=xyz\
&code_challenge=${CHALLENGE}&code_challenge_method=S256" \
  -b /tmp/sso.cookies | head -6

# 5. Exchange code for token (extract code from step 4)
curl -sk --resolve auth.inferia.local:443:127.0.0.1 \
  -X POST https://auth.inferia.local/oauth/token \
  -d "grant_type=authorization_code&code=${CODE}\
&client_id=inferiallm-dashboard\
&redirect_uri=https://inferia.local/auth/callback\
&code_verifier=verifier-1234567890abcdefghijklmnopqrstuvwxyzABCDEF"
```

The final token response carries an `access_token` JWT whose `iss` and
`aud` claims both equal `https://auth.inferia.local`, and an opaque
`refresh_token`. Verify the gateway can read it via `/oauth/userinfo`
with `Authorization: Bearer <access_token>`.

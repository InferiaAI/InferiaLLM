# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

InferiaLLM is an "operating system" for running LLMs in production. It provides a unified control/data plane for inference routing, access control, safety guardrails, compute orchestration, and observability. Python 3.10-3.12, FastAPI backend, React/TypeScript dashboard.

## Common Commands

```bash
# Setup & Run
make setup                    # Install environment and dependencies
make start                    # Run all services (inferiallm start)
inferiallm init               # Initialize database (first-time setup)
inferiallm start              # Start all microservices

# Testing
make test                     # Run all tests (pytest)
pytest package/src/inferia/services/api_gateway/tests/test_rbac.py  # Single test file
pytest -k "test_name"         # Run specific test by name

# Docker
make docker-build-unified     # Build unified Docker image
make docker-up-unified        # Start unified stack (app + postgres + redis + ES + logstash)
make docker-down              # Stop all services
make docker-clean             # Stop + remove volumes

# Dashboard (React)
cd apps/dashboard && npm run dev    # Dev server with HMR
cd apps/dashboard && npm run build  # Production build → package/src/inferia/dashboard/
cd apps/dashboard && npm run lint   # ESLint

# Package build
cd package && python -m build       # Build Python package
```

## Architecture

### Service Layout

All services live under `package/src/inferia/services/`:

| Service | Port | Role |
|---------|------|------|
| `api_gateway` | 8000 | Control plane: auth, RBAC, policy, audit |
| `inference` | 8001 | Data plane: inference request routing |
| `guardrail` | 8002 | Safety scanning, PII detection |
| `data` | 8003 | Knowledge base, RAG operations |
| `orchestration` | 8080 | Compute lifecycle, provider management |
| `filtration` | — | Combined alternative service mode |

### Service Startup Pattern

Each service follows: `main.py` → `start_api()` → `uvicorn.run("app:app")`. The CLI (`inferia.cli`) uses multiprocessing to launch services in parallel with queue-based IPC for status reporting.

### Inter-Service Communication

- **External**: REST/HTTP via FastAPI
- **Internal**: gRPC + protobuf (definitions in `services/orchestration/proto/v1/`)
- **Async tasks**: Redis Streams & Pub/Sub
- **Database**: PostgreSQL via async SQLAlchemy (asyncpg)

### Deployment Modes

- **Unified**: All services in one process/container (default)
- **Split**: Separate container per microservice (via Docker Compose profiles)

### Key Shared Code

- `package/src/inferia/common/` — logging, error schemas, shared utilities
- `package/src/inferia/infra/schema/` — SQL schemas and migration files
- `package/src/inferia/cli.py` — CLI entry point
- `package/src/inferia/startup_events.py` — service startup handlers

## Conventions

- **Config**: Pydantic `BaseSettings` with env var injection (per-service `config.py`)
- **Auth**: Stateless JWT + RBAC middleware; API keys for service-to-service
- **Errors**: Standardized `ErrorResponse` model + `APIError` exception class
- **Logging**: JSON structured logging with request ID tracking; optional Logstash integration (`[logstash]` extra)
- **Database migrations**: Raw SQL files in `infra/schema/migrations/`
- **Tests**: pytest + pytest-asyncio; fixtures in `conftest.py` provide mock DB sessions and httpx `AsyncClient`
- **Frontend**: React 19 + Vite + TailwindCSS + Shadcn/UI components; TanStack Query for server state

## Working Rules

- Never repeat the same mistake. When a mistake is made, always document it and its edge cases in the "Mistakes Log" section of this file so it is never repeated in future sessions and stays synced with collaborators.
- Refer to official web documentation whenever possible — do not guess at APIs, flags, or behavior.
- Security-first design: validate inputs at system boundaries, avoid command injection in `exec.Command` args, and never expose internal errors to API consumers.
- Design for scalability: prefer approaches that handle growing checkpoint counts, concurrent restores, and large archives without rearchitecting.
- Always use the superpowers plugin for planning and implementing features, debugging, and continuous development.

## Environment

Copy `.env.sample` to `.env` for local development. Key variables: `DATABASE_URL`, `REDIS_HOST`, `JWT_SECRET_KEY`, `INTERNAL_API_KEY`, `SECRET_ENCRYPTION_KEY`. Set `DATABASE_SSL=false` for local dev.

## CI/CD

- **Docker publish** (`.github/workflows/docker-publish.yml`): Builds multi-arch unified image → Docker Hub `inferiaai/inferiallm`
- **PyPI publish** (`.github/workflows/pypi-publish.yml`): Builds dashboard + sidecar + Python package → PyPI
- Both triggered on release/tag push (v*)

## Mistakes Log

<!-- Add entries here when mistakes are made during development. Format: -->
<!-- - **[DATE] Short description**: Root cause and fix. Edge cases to watch for. -->

- **[2026-05-12] Unified config: Pydantic Settings v2 source order is significant.** Appending vs. inserting a custom source changes precedence silently. Always assert order in a test (see `package/src/inferia/common/tests/unified_config/test_base.py::test_env_wins_over_yaml`). Edge case: a custom source returning `None` for a field still counts as "this source had no value" — only non-None values participate in the chain.
- **[2026-05-12] Unified config: `yaml.safe_load("")` returns `None`, not `{}`.** Wrap with `or {}` (or an explicit `is None` check) in `load_yaml` or the loader will `AttributeError` on the empty-file path. Edge case: a yaml file whose top level is a list or scalar also fails the `dict` contract — reject early with `ConfigParseError` rather than letting `**data` raise a confusing `TypeError`.
- **[2026-05-12] Unified config: `os.environ` reads at fork time.** Child multiprocessing workers see the parent's env *at fork*. Set `INFERIA_CONFIG` in the CLI *before* `multiprocessing.Process.start()`. Edge case: `spawn` start method on macOS/Windows also inherits env, but anything mutated after spawn won't propagate either way.
- **[2026-05-12] Unified config: `${VAR}` with an empty env var is not the same as unset.** `${VAR:-default}` treats empty as unset (falls back). `${VAR-default}` keeps the empty value. Mirror POSIX shell semantics; document the distinction at the call site. Edge case: `${A-B}` where `B` looks like a valid env-var name (`[A-Z_][A-Z0-9_]*$`) is rejected as ambiguous — use `${A:-B}` to pass a literal default that happens to look env-shaped.
- **[2026-05-12] Provider seeder: `SECRET_ENCRYPTION_KEY` must be a valid Fernet key.** A Fernet key is a URL-safe base64-encoded 32-byte random string. Any other value (e.g. a raw password, a hex string, or an AES-256-GCM key) causes `cryptography.fernet.Fernet.__init__` to raise and the seeder skips with a warning rather than crashing boot. Generate a key with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`. Edge case: an absent or empty `SECRET_ENCRYPTION_KEY` is safe — the seeder logs a clear message and skips, leaving existing DB rows untouched; it does NOT attempt to wipe existing credentials.
- **[2026-06-09] Catalog declare must resolve the service UUID by slug, not pass the slug.** InferiaAuth's `PUT /api/v1/services/:id/catalog` parses `:id` strictly as a UUID, and the service UUID is assigned **randomly per DB** (`upsertServiceForSlug` uses the DB-default), so it cannot be hardcoded. `rbac/catalog_declare.resolve_service_id()` lists `GET /api/v1/services` and matches `slug=="inferiallm"` before declaring to `…/services/{uuid}/catalog`. Edge case: an `EXTERNAL_SERVICE_ID` override skips the lookup; the resolve/declare are best-effort (return `False`, never raise) so a transient InferiaAuth outage doesn't crash boot — the catalog persists in InferiaAuth across restarts and re-declares idempotently next boot.
- **[2026-06-09] Two permission vocabularies — catalog keys must be bridged to local ones.** InferiaAuth tokens carry CATALOG keys (`inferiallm:org:read`), while the dashboard's `PermissionGuard`/`hasPermission` and backend route guards check the LOCAL `PermissionEnum` vocabulary (`organization:view`, `deployment:list`, …). Passing claims through verbatim locks every SaaS user out ("Required permission: organization:view"). `rbac/permissions.py::CATALOG_PERMISSION_MAP` + `expand_catalog_permissions()` is the bridge, applied in `_resolve_external_token` AND `_resolve_oidc_token` (originals kept, unknown keys pass through, provider has no local surface). Edge cases: when adding a catalog permission, add its mapping — `test_catalog_permission_map.py` pins map⊆catalog, catalog⊆map (minus provider), and that the admin role covers every SPA-gated string; the token's `roles` claim holds role-instance UUIDs (not names) — never gate on role names in external modes.
- **[2026-06-09] External modes need a shadow ORG, not just a shadow user — and identity gates must be per-endpoint.** The token's `org_id` in inferiaauth/oidc mode is the IdP org UUID with no local `organizations` row, while local features key on `user_ctx.org_id` (GET /organizations/me, audit FKs, API keys). Also, a ROUTER-level `require_local_identity` on the organizations router 409'd even read-your-own-org-context, locking the dashboard's main screen out. Fix: `rbac/external_org.ensure_external_org()` (called from both token resolvers) provisions a local org row for the IdP org id — name fetched from `GET {EXTERNAL_AUTH_URL}/api/v1/orgs/{id}` with the CALLER's bearer token, fallback `Organization <id8>` — plus a membership row; never raises. Guard rule: gate identity WRITES per-endpoint (create/update org, invitations); reads of the active org context stay open in all modes. Edge cases: `organizations.name` is UNIQUE — collisions get an id-suffixed name; the IdP fetch runs only on first sight of an org (not per request).
- **[2026-06-09] External-auth httpx calls must honor the custom CA.** In SaaS/SSO mode InferiaAuth sits behind a self-signed CA (Caddy `tls internal`), so token exchange (`rbac/oauth_client`), JWKS fetch (`rbac/jwks_verifier`), and catalog declare all `CERTIFICATE_VERIFY_FAILED` unless every `httpx` client passes `verify=config.httpx_verify(settings)` (= `ssl_ca_bundle` path if set, else the `verify_ssl` bool). Edge case: the **boot-time** declare in `app.py` is easy to miss — it calls `declare_catalog(...)` directly, so it must thread `verify=` + `service_id=` too, or only the boot path silently ignores the CA. Set `SSL_CA_BUNDLE` to a mounted CA bundle in the deployment (the SSO compose mounts Caddy's `sso-caddy-data` volume read-only).

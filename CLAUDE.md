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

## Reverse Proxy / Load Balancer Note

The application does **not** trust the `X-Forwarded-For` header from clients (to prevent rate limit bypass via header spoofing). If you deploy behind a reverse proxy (nginx, Caddy, ALB, etc.), you must configure uvicorn to trust your proxy so that `request.client.host` reflects the real client IP:

```bash
# Tell uvicorn to read proxy headers from a trusted proxy
uvicorn app:app --proxy-headers --forwarded-allow-ips="<proxy-ip-or-cidr>"
```

Without this, all requests behind a proxy will appear to come from the proxy's IP. See the [uvicorn proxy docs](https://www.uvicorn.org/settings/#http) for details.

## Environment

Copy `.env.sample` to `.env` for local development. Key variables: `DATABASE_URL`, `REDIS_HOST`, `JWT_SECRET_KEY`, `INTERNAL_API_KEY`, `SECRET_ENCRYPTION_KEY`. Set `DATABASE_SSL=false` for local dev.

## CI/CD

- **Docker publish** (`.github/workflows/docker-publish.yml`): Builds multi-arch unified image → Docker Hub `inferiaai/inferiallm`
- **PyPI publish** (`.github/workflows/pypi-publish.yml`): Builds dashboard + sidecar + Python package → PyPI
- Both triggered on release/tag push (v*)

## Mistakes Log

<!-- Add entries here when mistakes are made during development. Format: -->
<!-- - **[DATE] Short description**: Root cause and fix. Edge cases to watch for. -->

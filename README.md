<div align="center">

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="assets/readme-banner-dark.svg">
  <img alt="InferiaLLM — The operating system for LLMs in production" src="assets/readme-banner-light.svg" width="100%">
</picture>

[![PyPI](https://img.shields.io/pypi/v/inferiallm?style=flat-square&label=PyPI)](https://pypi.org/project/inferiallm/)
[![Docker](https://img.shields.io/docker/v/inferiaai/inferiallm?style=flat-square&label=Docker&sort=semver)](https://hub.docker.com/r/inferiaai/inferiallm)
[![License](https://img.shields.io/badge/license-Apache_2.0-green?style=flat-square)](./LICENSE)
[![Python](https://img.shields.io/badge/python-3.10+-blue?style=flat-square)](https://www.python.org/)
[![Status](https://img.shields.io/badge/status-beta-E8603C?style=flat-square)]()

**[Documentation](./apps/docs/README.md)** · **[Quick Start](#quick-start)** · **[Architecture](#architecture)** · **[Releases](https://github.com/InferiaAI/InferiaLLM/releases)** · **[Contributing](#contributing)**

</div>

---

## What is InferiaLLM

InferiaLLM is a self hosted operating system for running LLMs in production. It sits between your applications and your AI infrastructure, providing the platform primitives that enterprises need but nobody wants to build from scratch:

- **Access control and RBAC** — who can use which models, and how much
- **Safety guardrails** — PII detection, toxicity filtering, prompt injection defense
- **Inference routing** — failover, load balancing, backend selection
- **Compute orchestration** — provision and manage GPUs across clouds, on prem, and decentralized networks
- **Cost controls** — per user quotas, token budgets, rate limiting
- **Audit logging** — every request tracked, every policy decision recorded

These are operating system responsibilities. InferiaLLM provides them as a single, cohesive system.

> **Why this exists:** LLMs, inference engines, and GPUs are available — but they are not operable by organizations on their own. To run AI in production, teams end up building a fragmented platform across dozens of tools. InferiaLLM consolidates that entire layer.

---

## Quick Start

### Install from PyPI

```bash
pip install inferiallm==0.1.0b1
```

### Configure and run

```bash
# Download sample environment config
curl -o .env https://raw.githubusercontent.com/InferiaAI/InferiaLLM/main/.env.sample

# Set your credentials (database, Redis, secrets)
nano .env

# Initialize the database
inferiallm init

# Start all services
inferiallm start
```

That's it. Dashboard at `localhost:3001`, API gateway at `localhost:8000`, inference at `localhost:8001`.

<details>
<summary><strong>Build from source (for development)</strong></summary>
<br/>

```bash
git clone https://github.com/InferiaAI/InferiaLLM.git
cd InferiaLLM

python3 -m venv .venv && source .venv/bin/activate
pip install -e .

cp .env.sample .env
# Edit .env — set DB, Redis, and secrets

inferiallm init --env dev
inferiallm start all
```
</details>

<details>
<summary><strong>Run via Docker (recommended for production)</strong></summary>
<br/>

```bash
# Pull the official image
docker pull inferiaai/inferiallm:v0.1.0-beta.1

# Configure environment
curl -L https://raw.githubusercontent.com/InferiaAI/InferiaLLM/main/.env.sample -o .env
nano .env

# Run
docker run -d \
  --name inferia \
  --env-file .env \
  -p 8000:8000 -p 8001:8001 -p 8002:8002 -p 8003:8003 -p 8080:8080 -p 3000:3000 -p 3001:3001 \
  inferiaai/inferiallm:v0.1.0-beta.1
```

Or build from source with Docker Compose:

```bash
git clone https://github.com/InferiaAI/InferiaLLM.git && cd InferiaLLM
cp .env.sample .env

# Production
cd deploy && docker compose up -d --build

# Development — unified (monolithic)
docker compose -f docker-compose.profiles.yml --profile unified up --build

# Development — split (microservices)
docker compose -f docker-compose.profiles.yml --profile split up --build
```
</details>

---

## Architecture

InferiaLLM is split into two planes with clear separation of concerns.

```
┌──────────────────────────────────────────────────────┐
│                    CONTROL PLANE                     │
│              (policy, routing, compute)              │
│                                                      │
│  ┌──────────────────┐    ┌────────────────────────┐  │
│  │  API Gateway      │    │  Orchestration Gateway │  │
│  │                   │    │                        │  │
│  │  • Auth / RBAC    │    │  • Compute pools       │  │
│  │  • Policy engine  │◄──►│  • GPU provisioning    │  │
│  │  • Quota / budget │    │  • Backend scheduling  │  │
│  │  • Audit logging  │    │  • Provider routing    │  │
│  └─────────┬─────────┘    └────────────────────────┘  │
│            │                                          │
│  ┌─────────┴─────────┐    ┌────────────────────────┐  │
│  │  Guardrail Engine │    │  Data Engine            │  │
│  │                   │    │                        │  │
│  │  • PII scanning   │    │  • Knowledge base      │  │
│  │  • Toxicity       │    │  • Vector store        │  │
│  │  • Prompt inject. │    │  • Doc ingestion       │  │
│  └───────────────────┘    └────────────────────────┘  │
└────────────────────────────┬─────────────────────────┘
                             │ gRPC
┌────────────────────────────┴─────────────────────────┐
│                     DATA PLANE                       │
│                 (inference traffic)                   │
│                                                      │
│  ┌────────────────────────────────────────────────┐  │
│  │              Inference Gateway                  │  │
│  │                                                 │  │
│  │  • Request normalization                        │  │
│  │  • Policy evaluation (via control plane)        │  │
│  │  • Backend routing + failover                   │  │
│  │  • Response streaming                           │  │
│  └──────────┬──────────────┬──────────────┬────────┘  │
│             ▼              ▼              ▼           │
│         ┌───────┐    ┌──────────┐    ┌──────────┐    │
│         │ vLLM  │    │  Ollama  │    │ External │    │
│         │       │    │          │    │   APIs   │    │
│         └───────┘    └──────────┘    └──────────┘    │
└──────────────────────────────────────────────────────┘

         ▲                                    │
         │            REST / HTTP             │
    ┌────┴────┐                          ┌────▼────┐
    │  Your   │ ────── request ────────► │  Your   │
    │  App    │ ◄───── response ──────── │  App    │
    └─────────┘                          └─────────┘
```

**Data Plane** handles inference traffic via REST/HTTP (north south). The Inference Gateway normalizes requests, enforces policy, and routes to backends.

**Control Plane** handles policy and compute decisions via gRPC (east west). The API Gateway authenticates, the Orchestration Gateway manages compute, the Guardrail Engine scans for safety, and the Data Engine handles knowledge bases.

---

## Request Lifecycle

Every LLM request flows through a governed pipeline before reaching a model.

```
Request ──► Inference Gateway ──► API Gateway ──► Guardrail ──► Orchestration ──► Backend ──► Audit
            │                     │                │             │                │            │
            │ Normalize format    │ Auth (JWT)      │ PII scan    │ Select backend │ Execute    │ Log
            │ Forward to control  │ RBAC check      │ Toxicity    │ Route to       │ Stream     │ tokens,
            │ plane               │ Rate limits     │ Prompt inj. │ compute        │ response   │ latency,
            │                     │ Quota / budget   │ Block/pass  │                │            │ cost
            ▼                     ▼                ▼             ▼                ▼            ▼
```

**Rejected early.** Requests that fail policy or safety are blocked before inference. No compute is wasted on unauthorized or unsafe requests.

---

## Services

| Service | Port | What it does |
| :--- | :---: | :--- |
| **Dashboard** | `3001` | Admin UI — manage orgs, users, deployments, guardrails, API keys, audit logs |
| **API Gateway** | `8000` | Auth, RBAC, policy enforcement, quota management |
| **Inference Gateway** | `8001` | Data plane ingress for all LLM traffic |
| **Guardrail Engine** | `8002` | Content safety scanning, PII detection |
| **Data Engine** | `8003` | Knowledge base management, vector operations |
| **Orchestration Gateway** | `8080` | Compute lifecycle, backend routing, GPU provisioning |
| **DePIN Sidecar** | `3000` | Decentralized compute coordination |

---

## Compute Providers

InferiaLLM treats compute as a first class, governed resource. Providers are registered centrally, execution is scheduled through policy, and usage is tracked per request.

| Provider | Type | How it connects |
| :--- | :--- | :--- |
| **Nosana** | Decentralized GPU (DePIN) | Native sidecar integration |
| **AWS** | Cloud GPU (EC2) | Via SkyPilot |
| **GCP** | Cloud GPU (Compute Engine) | Via SkyPilot |
| **Akash** | Decentralized cloud | SDL based deployment |
| **Kubernetes** | On prem / managed clusters | Direct orchestration |

**Inference backends:** vLLM · Ollama · TEI · Infinity · LocalAI · Inferia Diffusion

**External API providers:** OpenAI · Anthropic · Gemini · Groq · Cerebras · Mistral · DeepSeek

---

## Configuration

InferiaLLM uses a `.env` file for configuration. Download `.env.sample` from the repo as a starting point.

### Database

| Variable | Description | Default |
| :--- | :--- | :--- |
| `PG_ADMIN_USER` | PostgreSQL admin username | `postgres` |
| `PG_ADMIN_PASSWORD` | PostgreSQL admin password | — |
| `DATABASE_URL` | Application database connection string | `postgresql://inferia:inferia@localhost:5432/inferia` |

### Security

| Variable | Description |
| :--- | :--- |
| `JWT_SECRET_KEY` | Signs access tokens. Min 32 characters. |
| `INTERNAL_API_KEY` | Authenticates service to service communication. Min 32 characters. |
| `SECRET_ENCRYPTION_KEY` | 32 byte base64 key for encrypting provider credentials. |
| `SUPERADMIN_EMAIL` | Initial admin login email. |
| `SUPERADMIN_PASSWORD` | Initial admin login password. |

```bash
# Generate keys
openssl rand -hex 32
```

> **Fail closed:** If `INTERNAL_API_KEY` is not set, all internal endpoints return 503. Missing config is treated as an error, never a bypass.

### Connectivity

| Variable | Description | Default |
| :--- | :--- | :--- |
| `REDIS_URL` | Redis connection | `redis://localhost:6379/0` |
| `DATABASE_URL` | PostgreSQL connection | `postgresql://inferia:inferia@localhost:5432/inferia` |

### Reverse Proxy

If deployed behind nginx, Caddy, or a load balancer, set `FORWARDED_ALLOW_IPS` to your proxy's IP so client IPs resolve correctly for rate limiting:

```bash
FORWARDED_ALLOW_IPS="10.0.0.1"
```

---

## CLI

```bash
inferiallm init                # Bootstrap database, roles, schemas
inferiallm migrate             # Apply pending migrations (runs automatically in Docker)
inferiallm start               # Start all services
inferiallm start inference     # Start only the Inference Gateway
inferiallm start api-gateway   # Start only the API Gateway
inferiallm start orchestration # Start only the Orchestration stack
```

---

## Tech Stack

| Layer | Technology |
| :--- | :--- |
| **Language** | Python 3.10+ |
| **API** | FastAPI (async) |
| **Inter service** | gRPC + Protobuf |
| **Database** | PostgreSQL 15 |
| **Cache / Broker** | Redis 7 (rate limiting, pub/sub, streams) |
| **Auth** | Stateless JWT (HS256) |
| **Encryption** | Fernet symmetric encryption |
| **Observability** | Prometheus compatible metrics (p50/p95/p99 latency, token throughput, error rates) |
| **Vector** | pgvector / ChromaDB compatible |

---

## Deployment

InferiaLLM is self hosted and cloud agnostic. It deploys to AWS, GCP, Azure, or bare metal without modification. The standard stack is Docker Compose with PostgreSQL and Redis.

It is **not** a model, runtime, or training system. It governs how those systems are used.

---

## Contributing

We welcome contributions. Each component has its own README with architecture context:

| Component | Responsibility | Documentation |
| :--- | :--- | :--- |
| **Orchestrator** | Compute lifecycle and workload management | [README](./src/orchestration/README.md) |
| **Guardrail Engine** | Content safety scanning and PII detection | [README](./src/guardrail/README.md) |
| **Data Engine** | Knowledge base and data processing | [README](./src/data/README.md) |
| **RBAC** | Identity and access boundaries | [README](./src/api_gateway/rbac/README.md) |
| **Gateway** | Secure internal service routing | [README](./src/api_gateway/gateway/README.md) |
| **Audit** | Immutable execution and policy logs | [README](./src/api_gateway/audit/README.md) |
| **Policy** | Quota, rate, and budget enforcement | [README](./src/api_gateway/policy/README.md) |
| **Prompt** | Prompt templates and versioning | [README](./src/api_gateway/prompt/README.md) |
| **Packages** | Installation, versioning, and initialization | [README](./README.md) |

Open an [issue](https://github.com/InferiaAI/InferiaLLM/issues) to report bugs or request features.

---

<div align="center">

**Own your intelligence.**

[inferia.ai](https://inferia.ai) · [X (Twitter)](https://x.com/inferiaai) · [LinkedIn](https://www.linkedin.com/company/inferiaai)

InferiaLLM - Copyright © 2026 Inferia AI · Licensed under the Apache License, Version 2.0

</div>

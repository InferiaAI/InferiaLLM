
<div align="center">

# InferiaLLM

### The Operating System for LLMs in Production

  [![License](https://img.shields.io/badge/license-Apache--2.0-green?style=flat-square)](./LICENSE)[![Python](https://img.shields.io/badge/python-3.10+-blue?style=flat-square)](https://www.python.org/)[![Status](https://img.shields.io/badge/status-beta-orange?style=flat-square)]()[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg?style=flat-square)](http://makeapullrequest.com)[![FastAPI](https://img.shields.io/badge/FastAPI-005571?style=flat-square&logo=fastapi)](https://fastapi.tiangolo.com/)[![Redis](https://img.shields.io/badge/Redis-DC382D?style=flat-square&logo=redis&logoColor=white)](https://redis.io/)[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-316192?style=flat-square&logo=postgresql&logoColor=white)](https://www.postgresql.org/)[![Kubernetes](https://img.shields.io/badge/kubernetes-%23326ce5.svg?style=flat-square&logo=kubernetes&logoColor=white)](https://kubernetes.io/)

</div>

  <br/>
  
  ```bash
  pip install inferiallm
  ```

  <br/>

  <img src="assets/inferia-cli.gif" width="100%" alt="Inferia CLI Demo" />

  <p>
    <a href="./apps/docs/README.md"><img src="https://img.shields.io/badge/Documentation-0078D4?style=for-the-badge&logoColor=white" height="30" alt="Documentation"></a>
    &nbsp;
    <a href="https://github.com/InferiaAI/InferiaLLM/issues"><img src="https://img.shields.io/badge/Issues-D73502?style=for-the-badge&logoColor=white" height="30" alt="Issues"></a>
    &nbsp;
    <a href="https://github.com/InferiaAI/InferiaLLM/releases"><img src="https://img.shields.io/badge/Releases-6f42c1?style=for-the-badge&logoColor=white" height="30" alt="Releases"></a>
  </p>

</div>

> [!IMPORTANT]  
> **Active Development**: InferiaLLM is currently in beta. While it is usable, Package may change as we finalize the control plane features.
> Your feedback is invaluable! Open [an issue](https://github.com/InferiaAI/InferiaLLM/issues) to report bugs or request features.

InferiaLLM acts as the **authoritative execution layer** between your applications and your AI infrastructure. It governs how LLMs are accessed, secured, routed, and run on compute.

---

## What “LLM Operating System” Means

LLMs, inference engines, and GPUs exist - but **they are not usable by organizations on their own**.

To operate LLMs in production, teams must build platform - level primitives:

* execution entry points
* access control and permissions
* safety enforcement
* resource limits and cost controls
* scheduling and routing
* compute lifecycle management
* auditing and observability

These are **operating system responsibilities**.

InferiaLLM provides these primitives as a single, cohesive system.
<div align="center">
  <img src="https://github.com/user-attachments/assets/3ad89406-a12b-4b70-b548-b45a9594dded" width="100%" alt="InferiaLLM Banner" />
</div>

---

## Quick Start

### 1. Simple Installation (via PyPI)

The easiest way to get started is to run InferiaLLM as a comprehensive Python package.

```bash
pip install inferiallm
```

**Setup & Configuration:**

> [!NOTE]
> InferiaLLM looks for a `.env` configuration file in your current working directory. You must create one to configure databases and secrets.

```bash
# 1. Download sample environment
curl -o .env https://raw.githubusercontent.com/InferiaAI/InferiaLLM/main/.env.sample

# 2. Configure your credentials (DB, Redis, Secrets)
# Ensure JWT_SECRET_KEY is at least 32 characters long
nano .env

# 3. Initialize database
inferiallm init

# 4. Start all services (API, Orchestration, Inference, Dashboard)
inferiallm start
```

### 2. Build from Source (Recommended for development)

If you want to contribute or modify the core logic:

```bash
# Clone repo
git clone https://github.com/InferiaAI/InferiaLLM.git
cd InferiaLLM

# Setup virtual environment
# Recommended: create venv at root for easy CLI access
python3 -m venv .venv
source .venv/bin/activate

# Install the package in editable mode
pip install -e package/

# Configure environment
cp .env.sample .env
# Edit .env (Set DB, Redis, and Secrets)

# Initialize databases and build frontend assets
# '--env dev' ensures dashboard and sidecar are rebuilt
inferiallm init --env dev

# Start all services (API, Orchestration, Inference, Dashboard)
inferiallm start all
```

### 3. Run via Docker (Recommended for Production)

We provide a unified, production-ready Docker image that contains the entire control plane. You can either use the official image from Docker Hub or build it locally.

#### Option A: Use Docker Hub (Fastest)

The official unified image is available on [Docker Hub](https://hub.docker.com/r/inferiaai/inferiallm).

```bash
# 1. Pull the official image
docker pull inferiaai/inferiallm:latest

# 2. Download and configure environment
curl -L https://raw.githubusercontent.com/InferiaAI/InferiaLLM/main/.env.sample -o .env
nano .env

# 3. Run the container
docker run -d \
  --name inferia-app \
  --env-file .env \
  -p 8000:8000 -p 8001:8001 -p 8002:8002 -p 8003:8003 -p 8080:8080 -p 3000:3000 -p 3001:3001 \
  inferiaai/inferiallm:latest
```

#### Option B: Build from Source

```bash
# 1. Clone the repository
git clone https://github.com/InferiaAI/InferiaLLM.git
cd inferiaLLM

# 2. Configure environment
cp .env.sample .env
# Edit .env to set your secrets

# 3. Build and start (Production Profile)
cd deploy
docker compose up -d --build
```

#### Option C: Development with Docker

For local development with source code mounting and profiles (unified or split):

```bash
# Unified Profile (Monolithic)
docker compose -f deploy/docker-compose.yml --profile unified up --build

# Split Profile (Microservices)
docker compose -f deploy/docker-compose.yml --profile split up --build
```

**Services will be available at:**

* **Dashboard:** `http://localhost:3001` (React/Vite Frontend)
* **Orchestration API:** `http://localhost:8080`
* **API Gateway:** `http://localhost:8000`
* **Inference Gateway:** `http://localhost:8001`
* **Guardrail Engine:** `http://localhost:8002`
* **Data Engine:** `http://localhost:8003`
* **DePIN Sidecar:** `http://localhost:3000`

 ---

## Configuration

InferiaLLM requires several environment variables to be configured in a `.env` file. You can find a template in `.env.sample`.

### 1. Database Setup (Required for `init`)

These variables are used by `inferiallm init` to bootstrap your database.

| Variable | Description | Default |
| --- | --- | --- |
| `PG_ADMIN_USER` | PostgreSQL admin username | `postgres` |
| `PG_ADMIN_PASSWORD` | PostgreSQL admin password | - |
| `DATABASE_URL` | Application database connection string | `postgresql://inferia:inferia@localhost:5432/inferia` |
| `INFERIA_DB` | (Optional) Override database name | `inferia` |

> [!TIP]
> `inferiallm init` will automatically extract the app-level database user, password, host, and port from your `DATABASE_URL`.

### 2. Security & Authentication

Essential for protecting your gateways and dashboard.

| Variable | Description |
| --- | --- |
| `JWT_SECRET_KEY` | Secret key for signing access tokens (Use a long random string, min 32 chars) |
| `INTERNAL_API_KEY` | Secret key for service-to-service communication |
| `SECRET_ENCRYPTION_KEY` | 32-byte base64 key for encrypting provider credentials |
| `SUPERADMIN_EMAIL` | Initial admin user email (used for Dashboard login) |
| `SUPERADMIN_PASSWORD` | Initial admin user password (used for Dashboard login) |

### 3. Service Connectivity

URLs and credentials for core infrastructure.

| Variable | Description | Default |
| --- | --- | --- |
| `REDIS_URL` | Redis connection URL | `redis://localhost:6379/0` |
| `DATABASE_URL` | Primary database URL (Postgres format) | `postgresql://inferia:inferia@localhost:5432/inferia` |

 ---

## CLI Reference

InferiaLLM provides a unified CLI to manage the platform.

### 1. `inferiallm init`

Initialize the control-plane databases, roles, and schemas.

**Expected Output:**

```text
[inferia:init] Connecting as admin
[inferia:init] Creating role: inferia_user
[inferia:init] Creating database: inferia
[inferia:init] Repairing privileges on inferia
[inferia:init] Applying schema: global_schema
[inferia:init] Bootstrapping API database (tables, default org, super admin)
...
[inferia:init] Bootstrap complete
```

If you already have an initialized database and only need the latest schema updates, run:

```bash
psql "$DATABASE_URL" -f db/migrations/20260212_add_inference_logs_ip.sql
```

### 2. `inferiallm start`

Start Inferia services. You can start all services at once or specific components.

**Usage:**

```bash
inferiallm start [service]
```

**Arguments:**

* `all`: Start all services (default)
* `orchestration`: Start Orchestration Gateway stack
* `inference`: Start Inference Gateway
* `api-gateway`: Start API Gateway

**Examples:**

```bash
# Start everything (Simplified Command)
inferiallm start

# Start everything (Explicit Command)
inferiallm start all

# Start only Orchestration
inferiallm start orchestration
```

**Expected Output (Unified):**

```text
[CLI] Starting All Services...
[Orchestration Gateway API] Listening on port 8080
[Inference Gateway API] Listening on port 8001
[API Gateway] Listening on port 8000
[Dashboard] Serving at http://localhost:3001/
...
```

### 3. Service Specific Commands

Instead of running everything, you can run individual gateways:

#### `inferiallm start orchestration`

Starts the Orchestration Gateway stack (API, Background Worker, and DePIN Sidecars).

#### `inferiallm start inference`

Starts the Inference Gateway standalone.

#### `inferiallm start api-gateway`

Starts the API Gateway standalone.

 ---

## Core Capabilities

InferiaLLM provides a **single control plane** for:

* LLM inference and deployment
* LLM access and proxying
* authentication, RBAC, and policy enforcement
* safety guardrails and request filtering
* usage, quota, and cost control
* inference routing and failover
* compute orchestration across heterogeneous infrastructure

 ---

## The Problem

Current LLM tooling focuses on:

* model training
* inference optimization
* GPU utilization

It does **not** address the operational reality of running LLMs for real users.

To deploy LLMs internally or in products, teams must independently build:

* API gateways
* authentication and RBAC
* safety and guardrails
* quota and budget enforcement
* usage and cost tracking
* inference routing logic
* GPU provisioning and scaling
* audit logging

These systems are usually:

* spread across many tools
* inconsistently implemented
* difficult to enforce centrally
* expensive to maintain

InferiaLLM consolidates this entire layer into **one operating system**.

 ---

## Scope and Responsibility

InferiaLLM is responsible for:

* LLM deployment and inference execution
* LLM proxying and access control
* authentication, authorization, and policy enforcement
* safety and request filtering
* backend selection and routing
* compute provisioning and lifecycle management
* usage, cost, and audit recording

InferiaLLM is **not** a model, runtime, or training system.
It governs how those systems are used.

 ---

## System Architecture

InferiaLLM is explicitly split into two planes:

* **Data Plane** – Handles inference traffic (North-South via REST/HTTP).
* **Control Plane** – Decides execution policy and routing (East-West via gRPC).

![System Architecture](assets/system_arch.png)

---

## Component Overview

### Applications (Entry Points)

These are the **only externally reachable services**.

| Service | Responsibility | Documentation |
| ------------------------- | ----------------------------------------------------------------------------- | ------------------------------------------------ |
| **Admin Dashboard** | Administrative control surface for policies, compute pools, usage, and audits | [README](./apps/dashboard/README.md) |
| **API Gateway** | Authentication, RBAC, policy enforcement | [README](./package/src/inferia/services/api_gateway/README.md) |
| **Inference Gateway** | Data-plane ingress for all LLM inference traffic | [README](./package/src/inferia/services/inference/README.md) |
| **Guardrail Engine** | Content safety scanning and PII detection | [README](./package/src/inferia/services/guardrail/README.md) |
| **Data Engine** | Data processing and knowledge base operations | [README](./package/src/inferia/services/data/README.md) |
| **Orchestration Gateway** | Compute control authority and execution routing | [README](./package/src/inferia/services/orchestration/README.md) |

---

## Technology Stack

InferiaLLM is built on a modern, high-performance foundation designed for scale and reliability.

### Core Runtime

* **Language**: Python 3.10+
* **API Framework**: FastAPI (Asynchronous, High-performance)
* **Inter-Service Communication**: gRPC (Protobuf)
* **Task Queue**: Redis Streams & Pub/Sub

### Data & State

* **Primary Database**: PostgreSQL 15 (Relational Data, JSONB for Audit Logs)
* **Cache & Broker**: Redis 7 (Rate Limiting, Hot State)
* **Vector Query**: Compatible with pgvector / ChromaDB (Sidecar support)

### Security

* **Authentication**: Stateless JWT (RS256)
* **Encryption**: Fernet (Symmetric encryption for secrets)
* **Policy Engine**: Custom RBAC with hierarchical permissions

---

### Inference Gateway (Data Plane)

* Entry point for all LLM requests
* Normalizes request formats
* Forwards requests for mandatory policy evaluation
* Routes approved requests to execution backends

Does **not** make policy or compute decisions.

#### Request Flow

![Request Flow](assets/request_flow.png)

#### Forwarding Client IP

If requests reach Inference Gateway through proxies or load balancers, forward client IP with one of:

* `X-IP-Address`
* `X-Client-IP`
* `X-Forwarded-For` (first IP is used)
* `X-Real-IP`

The value is stored in `inference_logs.ip_address` and can be used in Insights IP filtering.

 ---

### API Gateway (Policy Authority)

* Validates identity and permissions
* Enforces quotas, rate limits, and budgets
* Applies guardrails (PII, toxicity, prompt injection)
* Records structured audit data

Requests failing policy are rejected **before inference**.

 ---

### Orchestration Gateway (Compute Authority)

* Abstracts compute providers
* Manages compute pools
* Provisions and deprovisions resources
* Routes execution based on policy and availability

Supports:

* Kubernetes GPU clusters
* VPS infrastructure
* DePIN compute (e.g. Nosana)

### Admin Dashboard

* Manage organizations, users, and roles
* Define policies, budgets, and limits
* Register and manage compute providers
* Inspect usage, cost, and audit logs

---

### Guardrail Engine (Safety Authority)

* Scans prompts and responses for safety violations
* Detects PII (Personally Identifiable Information)
* Supports multiple safety providers (LLM Guard, Llama Guard, Lakera)
* Configurable scanners for toxicity, secrets, prompt injection

---

### Data Engine (Data Processing)

* Knowledge base management and vector operations
* Document ingestion and processing
* Data transformation and enrichment
* Integration with ChromaDB for vector storage

---

## Core Services (Control Plane Internals)

| Component | Responsibility | Documentation |
| ---------------- | ----------------------------------------- | --------------------------------------------------- |
| **Orchestrator** | Compute lifecycle and workload management | [README](./package/src/inferia/services/orchestration/README.md) |
| **Guardrail Engine** | Content safety scanning and PII detection | [README](./package/src/inferia/services/guardrail/README.md) |
| **Data Engine** | Knowledge base and data processing | [README](./package/src/inferia/services/data/README.md) |
| **RBAC** | Identity and access boundaries | [README](./package/src/inferia/services/api_gateway/rbac/README.md) |
| **Gateway** | Secure internal service routing | [README](./package/src/inferia/services/api_gateway/gateway/README.md) |
| **Audit** | Immutable execution and policy logs | [README](./package/src/inferia/services/api_gateway/audit/README.md) |
| **Policy** | Quota, rate, and budget enforcement | [README](./package/src/inferia/services/api_gateway/policy/README.md) |
| **Prompt** | Prompt templates and versioning | [README](./package/src/inferia/services/api_gateway/prompt/README.md) |
| **Packages** | Installation, versioning, and initialization | [README](./package/README.md) |

 ---

## Compute Control Model

InferiaLLM treats compute as a **first - class, governed resource**.

* Providers are registered centrally
* Execution is scheduled through policy
* Usage is tracked per request
* Environments are isolated

Compute decisions are made by the control plane - not application code.

 ---

## Audit and Observability

InferiaLLM records:

* request metadata
* policy decisions
* execution backend
* resource usage
* failure modes

This supports:

* cost attribution
* security review
* compliance
* incident investigation

### Metrics & Tracing

InferiaLLM exports **Prometheus-compatible metrics** from all gateways, providing visibility into:

* Request latency (p50, p95, p99)
* Token throughput per provider
* Error rates by model and tenant
* Active compute slot utilization

 ---

## Deployment Model

InferiaLLM is:

* **Self-Hosted**: Docker Compose standard stack (Postgres, Redis, Gateways).
* **Cloud-Agnostic**: Deploys to AWS, GCP, Azure, or bare metal without modification.
* **Provider-Neutral**: Supports any OpenAI-compatible inference backend (vLLM, TGI, Triton).

It integrates with existing infrastructure and avoids proprietary lock-in.

---

## Summary

InferiaLLM is the **operating system for LLMs in production**.

It provides:

* a single execution boundary
* enforced policy and security
* governed compute
* auditable operation

**From raw LLMs to real users - without building a platform from scratch.**

---

InferiaLLM  
Copyright © 2026 Inferia AI

InferiaLLM is an open-source LLM execution and control plane licensed under the Apache License, Version 2.0.

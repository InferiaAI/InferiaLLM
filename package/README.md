
<div align="center">

# InferiaLLM CLI

### The Operating System for LLMs in Production

  [![License](https://img.shields.io/badge/license-Apache--2.0-green?style=flat-square)](https://github.com/InferiaAI/InferiaLLM/blob/main/LICENSE)[![Python](https://img.shields.io/badge/python-3.10+-blue?style=flat-square)](https://www.python.org/)[![Status](https://img.shields.io/badge/status-beta-orange?style=flat-square)]()[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg?style=flat-square)](http://makeapullrequest.com)

</div>

InferiaLLM provides a unified CLI to manage the platform's control plane, initialize infrastructure, and orchestrate gateways (Orchestration, Inference, and Filtration).

---

## Installation

```bash
pip install inferiallm
```

---

## Quick Start

InferiaLLM requires a `.env` file in your current working directory to configure connections to PostgreSQL and Redis.

```bash
# 1. Initialize your environment
# Create a .env file with your DATABASE_URL and Redis settings
cp .env.sample .env

# 2. Bootstrap the platform
# This creates the database, roles, and applying schemas
inferiallm init

# 3. Launch all services
# Starts Orchestration, Inference, and Filtration gateways in a single process
inferiallm api-start
```

---

## Configuration

The CLI manages configuration through environment variables. The most critical settings are:

### 1. Database & Security
| Variable | Description | Default |
| --- | --- | --- |
| `DATABASE_URL` | Primary database connection string | `postgresql://inferia:inferia@localhost:5432/inferia` |
| `PG_ADMIN_USER` | Postgres admin user (required for `init`) | `postgres` |
| `PG_ADMIN_PASSWORD` | Postgres admin password (required for `init`) | - |
| `JWT_SECRET_KEY` | Secret for signing access tokens | - |
| `INTERNAL_API_KEY` | Secret for service-to-service auth | - |

### 2. Provider Specifics (Optional)
Required for provisioning compute from external providers:
* `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION`
* `AKASH_MNEMONIC`
* `NOSANA_WALLET_PRIVATE_KEY`

---

## CLI Reference

### `inferiallm init`
Bootstraps the unified database environment.
**Output:**
```text
[inferia:init] Connecting as admin to bootstrap inferia
[inferia:init] Creating role: inferia
[inferia:init] Creating database: inferia
[inferia:init] Applying schema: global_schema
[inferia:init] Bootstrap complete
```

### `inferiallm api-start`
Starts all InferiaLLM gateways (Orchestration, Inference, Filtration) and the Dashboard in one command.

### `inferiallm orchestration-gateway`
Starts the Orchestration Gateway standalone (manages compute and routing).

### `inferiallm inference-gateway`
Starts the Inference Gateway standalone (handles data-plane ingress).

### `inferiallm filtration-gateway`
Starts the Filtration Gateway standalone (enforces RBAC, quotas, and guardrails).

---

## Core Capabilities
* **Unified Control Plane**: Orchestrate LLMs across heterogeneous compute (K8s, DePIN, VPS).
* **Policy Enforcement**: Centralized RBAC, safety guardrails, and budget controls.
* **Execution Boundary**: Authority-based routing between applications and infrastructure.

---

For full documentation, architecture diagrams, and deployment guides, visit the [main repository](https://github.com/InferiaAI/InferiaLLM).

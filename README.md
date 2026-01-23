
<div align="center">

# InferiaLLM

### The Operating System for LLMs in Production

</div>
  [![License](https://img.shields.io/badge/license-Apache--2.0-green?style=flat-square)](./LICENSE)[![Python](https://img.shields.io/badge/python-3.10+-blue?style=flat-square)](https://www.python.org/)[![Status](https://img.shields.io/badge/status-beta-orange?style=flat-square)]()[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg?style=flat-square)](http://makeapullrequest.com)[![FastAPI](https://img.shields.io/badge/FastAPI-005571?style=flat-square&logo=fastapi)](https://fastapi.tiangolo.com/)[![Redis](https://img.shields.io/badge/Redis-DC382D?style=flat-square&logo=redis&logoColor=white)](https://redis.io/)[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-316192?style=flat-square&logo=postgresql&logoColor=white)](https://www.postgresql.org/)[![Kubernetes](https://img.shields.io/badge/kubernetes-%23326ce5.svg?style=flat-square&logo=kubernetes&logoColor=white)](https://kubernetes.io/)

  <br/>
  
  ```bash
  pip install inferiallm
  ```

  <br/>

  <img src="assets/inferia-cli.gif" width="100%" alt="Inferia CLI Demo" />

  <p>
    <a href="./docs/README.md"><img src="https://img.shields.io/badge/Documentation-0078D4?style=for-the-badge&logoColor=white" height="30" alt="Documentation"></a>
    &nbsp;
    <a href="https://github.com/InferiaAI/InferiaLLM/issues"><img src="https://img.shields.io/badge/Issues-D73502?style=for-the-badge&logoColor=white" height="30" alt="Issues"></a>
    &nbsp;
    <a href="https://github.com/InferiaAI/InferiaLLM/releases"><img src="https://img.shields.io/badge/Releases-6f42c1?style=for-the-badge&logoColor=white" height="30" alt="Releases"></a>
  </p>

</div>

> [!IMPORTANT]  
> **Active Development**: InferiaLLM is currently in beta. While it is usable, APIs may change as we finalize the control plane features.
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
  
 ---

## Quick Start

### 1. Manual Installation via Package

The easiest way to get started is to run Inferia as a comprehensive Python package.

```bash
pip install inferiallm
```

**Setup & Configuration:**

> [!NOTE]
> Inferia looks for a `.env` configuration file in your current working directory. You must create one to configure databases and secrets.

```bash
# 1. Download sample environment
curl -o .env https://raw.githubusercontent.com/InferiaAI/InferiaLLM/main/.env.sample

# 2. Configure your credentials (DB, Redis, Secrets)
nano .env

# 3. Initialize database
inferia init

# 4. Start all services
inferia api-start
```

### 2. Build from Source (Recommended for development)

If you want to contribute or modify the core logic:

```bash
# Clone repo
git clone https://github.com/InferiaAI/InferiaLLM.git
cd inferiaLLM

# Setup virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install in editable mode
pip install -e .

# Configure environment
cp .env.sample .env

# Initialize databases
inferia init

# Start API services
inferia api-start
```

### 3. Run via Docker (Recommended for reliable deployment)

We provide a production-ready `docker-compose` setup that orchestrates all gateways, databases, and queues.

```bash
# Clone the repository
git clone https://github.com/InferiaAI/InferiaLLM.git
cd inferiaLLM/deploy

# Configure environment
cp ../.env.sample .env
# Edit .env to set your secrets

# Start the stack
docker compose up -d
```

This will spin up:

* **Orchestration Gateway** (Port 8080)
* **Filtration Gateway** (Port 8000)
* **Inference Gateway** (Port 8001)
* **Postgres & Redis**

### 4. Automated Setup

Run the automated setup script to configure your environment, generate secrets, and install dependencies:

```bash
# Mac/Linux
make setup
# OR directly:
./setup_project.sh
```

Then start the API:

```bash
make start
```

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

| Service                   | Responsibility                                                                | Documentation                                    |
| ------------------------- | ----------------------------------------------------------------------------- | ------------------------------------------------ |
| **Admin Dashboard**       | Administrative control surface for policies, compute pools, usage, and audits | [README](./apps/dashboard/README.md)             |
| **Filtration Gateway**    | Authentication, RBAC, policy enforcement, and guardrails                      | [README](./apps/filtration-gateway/README.md)    |
| **Inference Gateway**     | Data-plane ingress for all LLM inference traffic                              | [README](./apps/inference-gateway/README.md)     |
| **Orchestration Gateway** | Compute control authority and execution routing                               | [README](./apps/orchestration-gateway/README.md) |

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

 ---

### Filtration Gateway (Policy Authority)

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

## Core Services (Control Plane Internals)

| Component        | Responsibility                            | Documentation                                       |
|  ----------------  |  -----------------------------------------  |  ---------------------------------------------------  |
| **Orchestrator** | Compute lifecycle and workload management | [README](./package/src/inferia/services/orchestration/README.md)        |
| **Guardrails**   | Safety enforcement and content filtering  | [README](./package/src/inferia/services/filtration/guardrail/README.md) |
| **RBAC**         | Identity and access boundaries            | [README](./package/src/inferia/services/filtration/rbac/README.md)      |
| **Gateway**      | Secure internal service routing           | [README](./package/src/inferia/services/filtration/gateway/README.md)   |
| **Audit**        | Immutable execution and policy logs       | [README](./package/src/inferia/services/filtration/audit/README.md)     |
| **Policy**       | Quota, rate, and budget enforcement       | [README](./package/src/inferia/services/filtration/policy/README.md)    |
| **Prompt**       | Prompt templates and versioning           | [README](./package/src/inferia/services/filtration/prompt/README.md)    |
| **Packages**     | Installation, versioning, and initialization | [README](./package/README.md)                    |

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

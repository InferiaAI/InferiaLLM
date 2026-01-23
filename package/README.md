# Inferia - Package installation & setup guide

This guide walks through local installation, database bootstrapping, and service startup.

## 1. Prerequisites

Before installing Inferia, ensure the following are available on your system:

> ### System Requirements

- [Python 3.12+](https://www.python.org/downloads/release/python-31212/)
- [PostgreSQL: 14+](https://www.postgresql.org/download/)
- [Node.js: 18+](https://nodejs.org/en/download)
- npm: Comes with Node.js

> ### Verify installation with this command

```bash
python3 --version
psql --version
node --version
npm --version
```

---

## 2. Create Python virtual environment

> Create and activate a virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate
```

> Upgrade pip

```bash
pip install --upgrade pip
```

> Install Inferia in editable mode:

```bash
pip install inferiallm
```

> Verify installation

```bash
inferia --help
```

----

## 4. Environment configuration

Inferia relies on environment variables for configuration.

> Create a .env file at the repository root:

```env
# AWS Configuration
AWS_ACCESS_KEY_ID=
AWS_REGION="ap-south-1"
AWS_SECRET_ACCESS_KEY=

# ChromaDB Configuration
CHROMA_API_KEY=
CHROMA_TENANT=

# Database Configuration (Common)
DATABASE_URL='postgresql+asyncpg://inferia:inferia@localhost:5432/inferia'
POSTGRES_DSN='postgresql://inferia:inferia@localhost:5432/inferia'


# Database Configuration (Postgres Specifics)
FILTRATION_DB='inferia'
INFERIA_DB_PASSWORD='inferia'
INFERIA_DB_USER='inferia'
ORCHESTRATION_DB='inferia'
PG_ADMIN_PASSWORD='inferia'
PG_ADMIN_USER='inferia'
PG_DB='inferia'
PG_HOST='localhost'
PG_PASSWORD='inferia'
PG_PORT='5432'
PG_USER='inferia'

# Groq Configuration
GROQ_API_KEY=
GROQ_MODEL=llama-guard-4-12b

# Guardrails Configuration
GUARDRAIL_ENABLE_BIAS=false
GUARDRAIL_ENABLE_CODE_SCANNING=false
# GUARDRAIL_ENABLE_LLM_GUARD_STARTUP=false # DEPRECATED
GUARDRAIL_ENABLE_NO_REFUSAL=false
GUARDRAIL_ENABLE_PROMPT_INJECTION=false
GUARDRAIL_ENABLE_RELEVANCE=false
GUARDRAIL_ENABLE_SECRETS=false
GUARDRAIL_ENABLE_SENSITIVE_INFO=false
GUARDRAIL_ENABLE_TOXICITY=false
GUARDRAIL_GROQ_API_KEY=
GUARDRAIL_LAKERA_API_KEY=
GUARDRAIL_PII_DETECTION_ENABLED=true

# Nosana Configuration
NOSANA_WALLET_PRIVATE_KEY=
SOLANA_RPC_URL=https://api.mainnet-beta.solana.com

# Redis Configuration
REDIS_DB="0"
REDIS_HOST=
REDIS_PASSWORD=
REDIS_PORT=6379
REDIS_USERNAME="default"

# Filtration Service Secrets
SUPERADMIN_PASSWORD="admin123"
INTERNAL_API_KEY="dev-internal-key-change-in-prod"
JWT_SECRET_KEY="dev-secret-key-change-in-production"
```

> ## Note
>
> `PG_ADMIN_*` is required only for `inferia init`
> Runtime services never use admin credentials.
---

## 5. Database initialization

Inferia provides a built-in bootstrap command that:

- Creates the inferia PostgreSQL role (if missing)
- Creates required databases
- Fixes schema ownership and privileges
- Applies SQL schemas
- Optionally resets filtration roles & members

Run:

```bash
inferia init
```

Expected output (example):

```bash
[inferia:init] Connecting as admin
[inferia:init] Role exists: inferia
[inferia:init] Database exists: inferia
[inferia:init] Database exists: filtration_gateway
[inferia:init] Repairing privileges on inferia
[inferia:init] Repairing privileges on filtration_gateway
[inferia:init] Applying schema: orchestration
[inferia:init] Bootstrap complete
```

> ## Idempotency gaurantee
>
> - Running `inferia init` multiple times is safe
> - Existing tables, enums, roles, and databases are skipped automatically
>
---

## 6. Nosana sidecar setup (Node.js)

Inferia includes a Node.js sidecar for ***Nosana integration***.

### Location

```text
inferia/services/orchestration/app/services/nosana-sidecar
```

On first run, dependencies are installed automatically.
You may also install them manually:

```bash
cd inferia/services/orchestration/app/services/nosana-sidecar
npm install
```

Ensure `package.json` exists in directory

---

## 7. Running Services

Inferia services can be started individually or together.

### 7.1 Run Individual Services

Filtration Gateway

```bash
inferia filtration-gateway
```

Inference Gateway

```bash
inferia inference-gateway
```

Orchestration Stack (API + Worker + Nosana Sidecar)

```bash
inferia orchestration-gateway
```

### 7.2 Run Everything (Recommended for Local Dev)

Start all services concurrently:

```bash
inferia api-start
```

This launches:

- Orchestration API
- Orchestration Worker
- Nosana Sidecar (Node.js)
- Inference Gateway
- Filtration Gateway
- Admin Dashboard (<http://localhost:3001>)

All services run in parallel using Python multiprocessing.

---

## 8. Development Notes

- `inferia init` is safe to re-run

- Sidecar runs as a child process (not embedded)
- No Docker required for local development
- Works cleanly inside `site-packages`

## 9. Production Recommendations

- Pre-build the Nosana sidecar (`tsc → dist/`)

- Replace `npx tsx` with `node dist/server.js`
- Use a process supervisor (systemd / PM2 / Kubernetes)
- Separate `.env` per service

---

> ## Summary
>
> ```bash
> git clone <repo>
> cd inferia
> python3 -m venv .venv
> source .venv/bin/activate
> pip install -e .
> cp .env.example .env
> inferia init
> inferia api-start
>  ```


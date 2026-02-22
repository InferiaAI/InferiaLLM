# API Gateway Service

The **API Gateway Service** (`services/api_gateway`) contains the core business logic for security, authentication, and policy enforcement in InferiaLLM. It is designed as a modular library that the API Gateway application wraps.

## Architecture

The service is composed of several independent components that work together to secure the LLM lifecycle.

```mermaid
graph TD
    Request --> Gateway
    
    subgraph "API Gateway Service Logic"
        Gateway[Gateway Logic/Router]
        
        %% Core Security
        Gateway -->|Auth| RBAC[RBAC]
        Gateway -->|Check| Policy[Policy & Quotas]
        Gateway -->|Scan| Engine[Guardrail Engine]
        Gateway -->|Render| Prompt[Prompt Engine]
        Gateway -->|Manage| Mgmt[Management/CRUD]
        
        %% Logging
        Gateway -->|Log| Audit[Audit Logger]
        
        %% Data Access Layer
        Mgmt -.->|CRUD| Data[Data Access Layer]
        
        %% Sub-systems
        subgraph "Guardrail System"
            Engine -->|Select| Prv{Provider}
            Prv -->|Default| LLM["LLM Guard (Local)"]
            Prv -->|Config| Llama["Llama Guard (API)"]
            Prv -->|Config| Lakera["Lakera Guard (API)"]
            
            Engine -->|Redact| PII[PII Service]
        end
    end
    
    %% Persistence
    RBAC --> DB[(PostgreSQL)]
    Policy --> Redis[(Redis)]
    Audit --> DB
    Data --> DB
```

## Component Modules

| Module | Description | Documentation |
| :--- | :--- | :--- |
| **`guardrail/`** | The core safety engine. Manages Providers (LLM/Llama/Lakera) and PII redaction. | [README](./guardrail/README.md) |
| **`rbac/`** | Role-Based Access Control. Handles JWT validation, user context, and permissions. | [README](./rbac/README.md) |
| **`gateway/`** | Service-level routing logic, internal API security, and rate limiting buckets. | [README](./gateway/README.md) |
| **`audit/`** | Centralized structured logging for security events and inference usage. | [README](./audit/README.md) |
| **`policy/`** | logic for enforcing usage quotas and injecting context based on policy. | [README](./policy/README.md) |
| **`prompt/`** | Template engine for rendering prompts with dynamic variables. | [README](./prompt/README.md) |

## Configuration

The service uses a centralized `config.py` in each module, typically loading from the shared `.env` file.

**Key Environment Variables:**

- **Authentication**: `JWT_SECRET_KEY`
- **Guardrails**: `GUARDRAIL_GROQ_API_KEY`, `GUARDRAIL_LAKERA_API_KEY`
- **Database**: `DATABASE_URL`

## Development

To run tests for the API Gateway logic:

```bash
# from root
pytest package/src/inferia/services/api_gateway/tests/
```

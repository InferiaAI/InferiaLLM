# InferiaLLM Admin Dashboard

The **Admin Dashboard** is the centralized control plane for InferiaLLM. It allows administrators to orchestrate compute, manage model deployments, configure guardrails, view execution logs, and manage organizational security.

## Tech Stack

- **Framework**: React 19 + Vite
- **Language**: TypeScript
- **Styling**: TailwindCSS, Shadcn/UI
- **State Management**: TanStack Query (React Query)
- **Routing**: React Router v7
- **Charts**: Recharts
- **Icons**: Lucide React
- **Notifications**: Sonner

## Key Features

### 1. Deployment Management
Full lifecycle management of LLM deployments (Inference & Training).
- **Status Monitoring**: Real-time status of replicas, health checks, and endpoints.
- **Logs**:
    - **Inference Logs**: View chat completion requests, responses, and latency.
    - **Terminal Logs**: Live stream of container stdout/stderr for debugging.
- **Configuration**:
    - **Guardrails**: Attach and configure safety scanners (Llama Guard, Toxicity, PII).
    - **RAG**: Connect deployments to Knowledge Base collections.
    - **Prompt Templates**: Version and manage system prompts.
    - **Rate Limits**: Configure TPM/RPM limits per deployment.
- **Training**: Specialized view for training workloads including TensorBoard integration and training metrics.

### 2. Compute Orchestration
Manage the underlying compute infrastructure.
- **Compute Pools**: Provision and manage pools of compute resources across heterogeneous providers.
- **Instance Details**: View detailed status, resource usage, and connectivity of compute instances.
- **Multi-Provider Support**: Seamlessly integrate with vLLM, DePIN networks (Nosana, Akash), and standard cloud providers.

### 3. Filtration & Security
Configure the policy and security layer.
- **Access Control (RBAC)**: granular management of Roles and Users.
- **API Keys**: Generate and manage API keys for programmatic access.
- **Security Settings**: Configure organization-wide security policies, including 2FA enforcement.
- **Audit Logs**: Comprehensive, immutable audit trail of all actions and inference requests.

### 4. Knowledge Base (RAG)
Manage data sources for Retrieval Augmented Generation (RAG) pipelines. Upload documents, manage collections, and link them to deployments.

### 5. Settings
- **Organization**: Manage organization profile and general settings.
- **Providers**: Configure and manage connections to external Model Providers (OpenAI, Anthropic) and Compute Providers.

## Getting Started

### Prerequisites
- Node.js 18+
- Running instance of InferiaLLM Backend (Orchestration & Filtration Gateways)

### Installation

```bash
cd apps/dashboard
npm install
```

### Environment Setup

Create a `.env` file in the `apps/dashboard` directory:

```env
VITE_API_BASE_URL=http://localhost:8000
VITE_ORCHESTRATOR_URL=http://localhost:8080
```

- `VITE_API_BASE_URL`: URL of the Filtration Gateway (Management API).
- `VITE_ORCHESTRATOR_URL`: URL of the Orchestration Gateway.

### Development

Start the development server:

```bash
npm run dev
```

The dashboard will be available at `http://localhost:3001` (default).

### Building for Production

```bash
npm run build
```

The output will be generated in the `dist` directory.

## Architecture & Integration

The dashboard acts as a frontend for the InferiaLLM Control Plane.

- **Filtration Gateway Connection**: Used for authentication, RBAC, user management, and high-level policy configuration.
- **Orchestration Gateway Connection**: Used for "Day 2" operations—deploying models, managing compute pools, and viewing live deployment status. Use `VITE_ORCHESTRATOR_URL` to configure this connection.
- **Inference Gateway Interaction**: The dashboard does **not** directly handle inference traffic. It visualizes logs and metrics that are asynchronously reported by the Inference Gateway to the control plane.

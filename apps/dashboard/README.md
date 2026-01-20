# Admin Dashboard

The **Admin Dashboard** is the control plane for InferiaLLM. It allows administrators to manage deployments, configure guardrails, view inference logs, and manage API keys.

## Tech Stack

- **Framework**: React (Vite)
- **Styling**: TailwindCSS + Shadcn/UI
- **Icons**: Lucide React
- **Graphs**: Recharts

## Key Features

- **Deployment Management**: Create and configure model deployments.
- **Guardrail Configuration**:
  - Select Guardrail Engine (LLM Guard, Llama Guard, Lakera).
  - Configure granular scanners (Toxicity, PII, etc.).
  - Set thresholds.
- **Inference Logs**: View real-time logs of requests, including blocked prompts and latency.
- **RAG & Data**: Manage knowledge base collections.

## Setup

1. **Install Dependencies**

   ```bash
   npm install
   ```

2. **Environment Setup**
   Create `.env`:

   ```env
   ```env
   VITE_API_BASE_URL=http://localhost:8000
   VITE_ORCHESTRATOR_URL=http://localhost:8080
   ```

3. **Run Development Server**

   ```bash
   npm run dev
   ```

## linking

- Connects to **Filtration Gateway** (`apps/filtration-gateway`) for policy and high-level management.
- Connects to **Orchestration Gateway** (`apps/orchestration-gateway`) for compute pools and model deployments.
- Does **not** interact directly with the Inference Gateway's data plane.

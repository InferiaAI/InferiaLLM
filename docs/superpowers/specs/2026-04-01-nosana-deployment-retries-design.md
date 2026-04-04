# Deployment Failure Retry Logic

**Issue:** #167 — Add retries on Nosana deployment failures
**Date:** 2026-04-01

## Problem

Nosana deployments sometimes fail due to CUDA API incompatibility or other transient errors. Currently, a single failure immediately marks the deployment as `FAILED`, requiring the user to manually restart. We need automatic retry logic so deployments can recover from transient failures without user intervention.

## Decisions

| Decision | Choice |
|----------|--------|
| Scope | All providers (not just Nosana) |
| Retry strategy | Same node first, then re-provision on new node |
| Retry limit | Configurable via `MAX_DEPLOYMENT_RETRIES` env var, default 2 retries |
| Visibility | New `RETRYING` state visible in dashboard |
| Backoff | Exponential: 10s, 20s (10 * 2^attempt) |
| Approach | Worker-level retry loop (Approach A) |

## Architecture

### 1. Retry Loop in Worker

In `worker.py`'s `handle_deploy_requested()`, wrap the existing provision -> wait -> deploy logic in a retry loop:

1. On job failure (adapter raises `RuntimeError` from `wait_for_ready()` or strategy deployment), enter retry path
2. **First retry:** attempt restart on the same node — call `adapter.deprovision_node()` then `adapter.provision_node()` with same metadata (different CUDA allocation may succeed)
3. **Subsequent retries:** deprovision failed node, provision a fresh node (different hardware)
4. Track attempt count. After `MAX_DEPLOYMENT_RETRIES` (default 2) retries exhausted, mark `FAILED` with the last error
5. **Exponential backoff:** sleep `10 * 2^attempt` seconds between retries (10s, 20s)

The retry loop wraps only the provision -> wait_for_ready -> strategy execution section. Node candidate finding (`MAX_PROVISION_RETRIES`) stays as-is within each attempt.

### 2. RETRYING State & Tracking

- Add `RETRYING` as a valid deployment state. When entering a retry, transition the deployment to `RETRYING` with `error_message` set to e.g., `"Retry 1/2: Nosana job entered terminal state: FAILED"`
- Track retry metadata in the existing `configuration` JSONB column — no schema migration needed:
  - `configuration["retry_count"]` — current attempt number (0-indexed)
  - `configuration["max_retries"]` — limit for this deployment (from config)
  - `configuration["last_retry_error"]` — error from the last failed attempt
- The `RETRYING` state is visible via the existing `GET /deployments` endpoint and `deployment.state_changed` Redis event — the dashboard can show it without new API work
- Config: `MAX_DEPLOYMENT_RETRIES` env var, added to orchestration `Settings` with default `2`

### 3. Dashboard Visibility

In the deployment list/detail views on the dashboard, the `RETRYING` state needs to render:

- A new status badge color for `RETRYING` — yellow/amber with a retry icon, showing the attempt info from `error_message` (e.g., "Retry 1/2: ...")
- No new API endpoints — the existing deployment list endpoint already returns `state` and `error_message`

Minimal frontend change — just adding a case to the state rendering logic wherever deployment status badges appear.

### 4. Error Handling & Edge Cases

- **User cancels during retry:** If `model.terminate.requested` arrives while in `RETRYING` state, the worker's terminate handler treats it like any active deployment — deprovision and mark `STOPPED`. The retry loop checks if the deployment is still in `RETRYING` state before each attempt (CAS check), so a concurrent termination causes it to bail out.
- **Transient vs permanent failures:** No classification in this iteration — all failures get retried equally. If CUDA incompatibility persists across all nodes, it exhausts retries and marks `FAILED` with accumulated error context.
- **Same-node restart failure:** If the first retry (same-node) fails, subsequent retries provision fresh nodes. If provisioning itself fails, the existing `MAX_PROVISION_RETRIES` handles capacity exhaustion within that attempt.
- **Backoff interrupted by shutdown:** If the worker process dies during a backoff sleep, the deployment stays in `RETRYING` state. The existing health check loop (30s interval, 120s heartbeat timeout) will eventually detect the stale deployment and mark it `FAILED`.

## Files to Modify

| File | Change |
|------|--------|
| `package/src/inferia/services/orchestration/services/model_deployment/worker.py` | Add retry loop wrapping provision -> wait -> deploy, exponential backoff, same-node-first logic |
| `package/src/inferia/services/orchestration/config.py` | Add `MAX_DEPLOYMENT_RETRIES` setting (default 2) |
| `package/src/inferia/services/orchestration/repositories/model_deployment_repo.py` | Allow `RETRYING` state transitions, update configuration JSONB with retry metadata |
| Dashboard deployment status components | Add `RETRYING` state badge rendering |

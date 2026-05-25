# AWS Compute Node Provisioning UX

**Status:** Draft
**Date:** 2026-05-25
**Scope:** InferiaLLM dashboard + orchestration service

## Goal

When a user creates an AWS compute node from the dashboard, the EC2 instance is provisioned eagerly and the node detail page surfaces granular allocation/creation status. The Logs and Web Shell tabs also become available for AWS nodes, matching the behavior of the existing inferia-worker node detail view.

## Problem

Today (post `156a44c` on `main`):

- `POST /deployment/createpool` with `provider=aws` writes a placeholder `compute_inventory` row marked `state='ready'` and **does not** call `PulumiAWSAdapter.provision_node` (`deployment_server.py:1099`). The EC2 instance is created lazily, only when a model is deployed onto the pool.
- `PulumiAWSAdapter._provision_async` (`pulumi_aws_adapter.py:272`) writes outputs to `compute_pools.metadata` on success and `lifecycle_state='failed'` on failure, with no progress reporting in between. The fact that there is no progress reporting hides the 30-120s creation window during which the user cannot tell whether anything is happening.
- `InstanceDetail.tsx:206` gates the Logs and Shell tabs on `agent_kind === 'worker'`. AWS nodes inherit this once their worker bootstraps, but during the provisioning window there is no log or shell surface at all.

## Decisions

| Question | Choice |
|---|---|
| When does the EC2 instance get provisioned? | Eagerly at pool creation. |
| What does the Logs tab show during provisioning? | Pulumi events + EC2 console output. |
| What backs the Web Shell tab? | Existing worker-WS shell only (post-bootstrap). |

## Phase model

Eight phases, each with status `pending | running | succeeded | failed`. On any failure, that phase flips to `failed`, downstream phases stay `pending`, and a surface-safe error message is recorded. The inventory row's `state` stays at `'provisioning'` while `_provision_async` runs `stack.destroy()`, then transitions to `'terminated'` (the `compute_inventory.state` enum has no `'failed'` value). The truth surface for failure is the provisioning summary: `terminal=true` plus any phase with `status='failed'`. `compute_pools.lifecycle_state='failed'` continues to record the pool-level failure for the existing reporting path.

| # | Phase | What runs | Source of truth |
|---|---|---|---|
| 1 | `prepare` | load `ProvidersConfig`, validate metadata, mint bootstrap token, build user-data | adapter |
| 2 | `ami_lookup` | `latest_dlami_ami()` (skipped if AMI pinned in metadata or `ProvidersConfig.cloud.aws.ami_id`) | adapter |
| 3 | `pulumi_init` | `create_or_select_stack`, `set_config` | adapter |
| 4 | `pulumi_up` | `stack.up(on_event=…)` — engine events sub-streamed | Pulumi `on_event` callback |
| 5 | `ec2_running` | EC2 instance reaches `running`; instance_id + public_dns captured | Pulumi `res_outputs_event` |
| 6 | `cloud_init` | user-data runs (apt-get, docker pull `ghcr.io/inferiaai/inferia-worker`, container start) | `ec2.get_console_output()` polled every 15s, best-effort (AWS only refreshes console output every 4-15 min) |
| 7 | `worker_bootstrap` | worker contacts control plane, opens control WS, calls `register_worker` | `compute_inventory.state` transition |
| 8 | `ready` | placement reports worker as healthy | `compute_inventory.state='ready'` |

## Backend changes

### New table: `node_provisioning_events`

```sql
CREATE TABLE node_provisioning_events (
  id           BIGSERIAL PRIMARY KEY,
  pool_id      UUID NOT NULL,
  node_id      UUID,                   -- nullable until inventory row exists
  phase        TEXT NOT NULL,          -- 'prepare' | 'ami_lookup' | 'pulumi_init' |
                                       -- 'pulumi_up' | 'ec2_running' | 'cloud_init' |
                                       -- 'worker_bootstrap' | 'ready'
  status       TEXT NOT NULL,          -- 'pending' | 'running' | 'succeeded' | 'failed' | 'log'
  message      TEXT,                   -- human text or JSON-encoded Pulumi event
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ix_node_provisioning_events_pool_id_id
  ON node_provisioning_events (pool_id, id);
```

Append-only; one row per status transition plus one per `log` event from the Pulumi `on_event` callback and the cloud-init console poller. Migration lives in `package/src/inferia/infra/schema/migrations/`.

### New repository: `node_provisioning_repo.py`

In `package/src/inferia/services/orchestration/repositories/`:

- `append_event(pool_id, phase, status, message, node_id=None) -> int`
- `list_events_after(pool_id, after_id, limit=500) -> list[Event]`
- `summarize_phases(pool_id) -> dict[phase, {status, started_at, ended_at, last_message}]`
- `current_phase(pool_id) -> str | None`

### Adapter changes (`pulumi_aws_adapter.py`)

- `provision_node()` accepts an optional `progress_writer: Callable[[str, str, str|None], None]` parameter; when omitted, defaults to a no-op (preserves backward compatibility with the lazy-deploy path).
- `_provision_async()` writes `('phase', 'running' | 'succeeded' | 'failed', message)` at each phase boundary. The existing `provider_instance_id=None` return shape stays unchanged for callers that don't pass a writer.
- `stack.up()` is invoked with `on_event=lambda ev: progress_writer('pulumi_up', 'log', _serialize_event(ev))`. `_serialize_event` flattens `resource_pre_event`, `res_outputs_event`, `diagnostic_event`, and `summary_event` into a JSON string with `{kind, urn, type, op, message}`.
- A sibling `asyncio.Task` runs during phases 5-6: every 15s, fetch `ec2.get_console_output()`, diff against the last fetched text, and append any new lines as `('cloud_init', 'log', new_line)`. Task stops when phase 7 begins or after 10 minutes (configurable via `settings.aws_cloud_init_poll_timeout_s`).
- On any exception in `_provision_async`, the failing phase's row is written as `'failed'` with `str(e)` truncated to 1 KiB.

### `/createpool` flow change (`deployment_server.py:1099`)

For `provider in ('aws',)` (other clouds remain as today; this spec scopes only AWS):

1. Placeholder inventory insert: change `state='ready'` → `state='provisioning'`, `gpu_total=0`. The placement filter (`placement_repo.py:48`) already rejects `gpu_total < gpu_req`, so this prevents any scheduling onto a still-provisioning node.
2. After `RegisterPool` returns, call:
   ```python
   adapter = get_adapter('aws')
   await adapter.provision_node(
       provider_resource_id=req.allowed_gpu_types[0],
       pool_id=resp.pool_id,
       org_id=req.owner_id,
       region=req.region_constraint,
       use_spot=req.use_spot,
       progress_writer=_writer_for_pool(resp.pool_id),
   )
   ```
3. On Pulumi success (inside `_provision_async`), update the placeholder row:
   ```sql
   UPDATE compute_inventory
   SET provider_instance_id = $1,    -- real EC2 instance id from outputs
       hostname             = $2,    -- public_dns
       gpu_total            = $3,    -- from req.gpu_count
       updated_at           = now()
   WHERE pool_id = $4
     AND provider_instance_id LIKE 'placeholder:%'
   ```
   The worker's subsequent `register_worker` call upserts on `(provider, provider_instance_id)` and finds the same row, transitioning `state` to `ready`.
4. On Pulumi failure: write the `failed` phase event, run `stack.destroy()` (existing behavior), then set `compute_inventory.state='terminated'` and append `metadata.failure_reason = "<surface-safe message>"`. Also keep the existing `compute_pools.lifecycle_state='failed'` write so legacy callers still see the pool-level failure.

The placeholder is **updated in place**, not deleted and re-inserted, so the `node_id` URL the dashboard navigated to remains valid throughout the lifecycle.

### New REST endpoints (`api/nodes.py`)

All gated by `deployment:read`.

```
GET /v1/nodes/{node_id}/provisioning
→ {
    current_phase: str | null,
    terminal: bool,
    phases: [
      {phase, status, started_at, ended_at, last_message}, ... (8 entries)
    ]
  }

GET /v1/nodes/{node_id}/provisioning-logs?after=<id>&limit=500
→ {
    events: [{id, phase, status, message, created_at}, ...],
    next_after: int | null
  }

GET /v1/nodes/{node_id}/ec2-console
→ {
    logs: [string, ...],
    fetched_at: iso8601
  }
```

`/ec2-console` proxies the existing `PulumiAWSAdapter.get_logs()` method, which calls `boto3 ec2.get_console_output(InstanceId=...)`. Returns the latest snapshot; the dashboard fetches it manually (not on a poll), because AWS only refreshes this every 4-15 minutes.

## Frontend changes (`apps/dashboard/`)

### `pages/Compute/NewPool.tsx`

After `POST /deployment/createpool` returns for `provider='aws'`:
1. Call `listNodes({ labels: { pool_id: resp.pool_id } })` (or a `GET /v1/nodes?pool_id=` if added) to resolve the placeholder `node_id`.
2. `navigate(\`/dashboard/compute/nodes/${node_id}?tab=overview\`, { replace: true })`.

### `pages/Compute/InstanceDetail.tsx`

- **Tab visibility:** relax `isWorker` to `showLogsAndShell = isWorker || node.provider === 'aws'`.
- **Poll cadence:** `2000` ms when `state in ('ordered','provisioning')`, `15_000` ms otherwise.
- **Overview tab:** when `node.provider === 'aws'` and (`state === 'provisioning'` OR provisioning summary `terminal=true && any phase failed`), render a new `<ProvisioningStatus />` card above the existing Node Information card. The card:
  - Lists all 8 phases with a status icon (spinner / check / X / dim circle).
  - Shows the latest `last_message` for the current `running` phase.
  - Shows total elapsed wall time and per-phase elapsed.
  - On `terminal && any failed`: renders a red banner with the failed phase's `last_message`.
- Data source: `useEffect` polling `GET /v1/nodes/{id}/provisioning` every 2s while `!terminal`.

### `components/nodes/NodeLogs.tsx`

Branch on `node.state` and `node.provider`:

- `state === 'ready'` → existing worker WS log stream (unchanged behavior).
- `provider === 'aws' && (state === 'provisioning' || (state === 'terminated' && provisioning summary has failed phase))` → poll `GET /v1/nodes/{id}/provisioning-logs?after=<lastId>` every 2s; stop polling once `terminal=true`. Render each line with a phase tag in muted color; Pulumi `diagnostic_event` lines render red; cloud-init lines render dim. A "Fetch EC2 console" button at the top right calls `/v1/nodes/{id}/ec2-console` and prepends the result in a collapsible panel.

### `components/nodes/NodeShell.tsx`

Branch on `node.state`:

- `state === 'ready'` → existing worker WS shell (unchanged).
- Otherwise → disabled state with text *"Shell available once the worker registers. Currently {current_phase}…"* Reads `current_phase` from the same `/provisioning` poll that `<ProvisioningStatus />` uses (lifted to `InstanceDetail` and passed down).

### New component: `components/nodes/ProvisioningStatus.tsx`

Pure presentational, takes `{ phases, currentPhase, terminal, elapsedSeconds }`. Vitest-only tests.

## Tests

Per repo convention and user CLAUDE.md: pytest + Vitest, ≥95% coverage on touched code, all edge cases.

### Backend pytest

- `repositories/test_node_provisioning_repo.py`
  - append / list_events_after pagination
  - list_events_after with no events / empty pool / unknown pool
  - summarize_phases ordering, missing phases default to `pending`
  - current_phase returns the latest `running` phase, falls back to last `succeeded` when terminal
- `services/adapter_engine/adapters/pulumi/test_pulumi_aws_adapter_progress.py`
  - mock `pulumi.automation.create_or_select_stack` and `stack.up`; verify all 8 phase rows written in order
  - mock `on_event` to call back with each event kind; verify JSON serialization
  - `stack.up` raises mid-flight → current phase = `failed`, downstream phases untouched
  - AMI lookup failure → `ami_lookup`=`failed`, no `pulumi_init` row
  - placeholder swap UPDATE runs after Pulumi success
  - cloud-init poll timeout after 10 min → no further `cloud_init` events
  - cloud-init poll: identical console output emits zero events (diff-only)
- `services/model_deployment/test_createpool_aws_eager.py`
  - `POST /createpool provider=aws` triggers `adapter.provision_node` (mocked)
  - placeholder inventory row inserted with `state='provisioning'`, `gpu_total=0`
  - lazy path for `provider in ('nosana','akash')` unchanged (regression)
  - AWS metadata validation error still returns 422 before provisioning starts
- `api/test_nodes_provisioning_endpoints.py`
  - `GET /provisioning` 200 / 404 / 403
  - `GET /provisioning-logs?after=` cursor advances
  - `GET /ec2-console` proxies adapter; returns `{logs: []}` when console not yet available
  - all three endpoints reject without `deployment:read`

### Frontend Vitest

- `components/nodes/ProvisioningStatus.test.tsx`
  - renders all 8 phases in correct order
  - spinner only on `running`; check on `succeeded`; X on `failed`; dim circle on `pending`
  - failed phase renders red banner with `last_message`
- `components/nodes/NodeLogs.test.tsx`
  - `state='ready'` opens worker WS (existing behavior preserved)
  - `state='provisioning'` + `provider='aws'` polls `/provisioning-logs?after=<id>`
  - `after` cursor advances
  - "Fetch EC2 console" button calls `/ec2-console` and renders snapshot
- `components/nodes/NodeShell.test.tsx`
  - `state='ready'` enables WS shell
  - `state='provisioning'` shows disabled placeholder with `current_phase` text
- `pages/Compute/InstanceDetail.test.tsx`
  - fast poll (2s) during `provisioning`, slow poll (15s) when `ready`
  - tab visibility for `provider='aws'` includes Logs and Shell

## Out of scope

- GCP / Azure equivalent provisioning UX — same shape, separate spec.
- SSH or SSM shell into the EC2 host pre-bootstrap.
- Per-step retry or cancel (Pulumi `cancel()` plumbing is its own work).
- Multi-instance AWS pools — today's `provision_cluster` creates one EC2 per pool; that constraint stays.
- Streaming Pulumi events via WebSocket — REST polling at 2s suffices for a 30-120s flow.

## Risks

- **Pulumi sync API blocking the event loop.** Mitigated by the existing `asyncio.to_thread(stack.up)` pattern. The `on_event` callback runs on the Pulumi thread; it dispatches into the async progress writer via `asyncio.run_coroutine_threadsafe(...)`. The writer is non-blocking (asyncpg insert, no waits).
- **Placeholder swap race.** Worker bootstrap could call `register_worker` before `_provision_async` finishes the UPDATE swap. The existing `(provider, provider_instance_id)` upsert in `inventory_repo.heartbeat` handles this by inserting a fresh row; the swap then becomes a no-op. Tested explicitly.
- **EC2 console output staleness.** AWS only refreshes console output every 4-15 min. The 15s poll is best-effort — we document this in the UI ("EC2 console updates every few minutes; click to refresh"). For real-time cloud-init visibility, users would need SSM, which is out of scope.
- **Pulumi failure mid-stream leaves orphan resources.** `_provision_async` already calls `stack.destroy` on failure (`pulumi_aws_adapter.py:306`). The new failure-event write happens before destroy, so the UI shows the failure cause even if destroy itself raises.
- **Provisioning event table growth.** Each pool produces ~50-500 events. With a daily cleanup job (out of scope here), the table stays small. Note in implementation plan.

## Open items resolved during brainstorming

- Phase granularity → 8 phases (user approved).
- Placeholder semantics → updated in place, not deleted (user approved).
- Logs tab content → Pulumi events + EC2 console output (user approved).

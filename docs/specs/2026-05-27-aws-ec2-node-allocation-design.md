# AWS EC2 Node Allocation — Robustness Refactor

**Status:** Draft
**Date:** 2026-05-27
**Scope:** InferiaLLM orchestration service + dashboard + inferia-worker (`feat/aws-ec2-bootstrap`)
**Supersedes (in part):** [`2026-05-25-aws-node-provisioning-ux.md`](2026-05-25-aws-node-provisioning-ux.md) — the UX surface from that spec stays; the backend `_provision_async` path described there is replaced by the reconciler pattern below.

## Goal

Make AWS EC2 node allocation work end-to-end and stay working under failures. Today, clicking *Create AWS pool* in the dashboard inserts a `compute_inventory` row, then the EC2 instance is never created and no error surfaces. After this refactor:

1. Every provision either succeeds or lands in a **terminal `failed` state with a typed error code, human message, and actionable hint**. No silent no-ops.
2. The dashboard's *Overview* sub-tab on the node detail page shows live provisioning phase + AWS metadata (instance ID, public DNS, region, AMI) + a Retry button on failure.
3. Killing `inferia-app` mid-`pulumi up` is safe — the next reconciler instance resumes the job from the DB.
4. The wizard's 3-way instance class selector (Normal GPU / Heavy GPU / CPU only) — already partially built in `0783fe6` — gains real backend semantics: CPU nodes use a plain Ubuntu AMI, skip the NVIDIA driver setup, advertise `gpu=0`, and the worker accepts CPU-friendly engine deploys.

## Non-Goals

- Multi-region failover, cross-region replication.
- Autoscaling pools (auto-add/remove EC2 instances based on load).
- A live cloud-init / EC2 console-log tail on the *Overview* tab — that stays in the *Logs* sub-tab.
- Real-time AWS pricing — use a static curated catalog with approximate prices.
- Resizing an existing node (operator deletes + recreates).
- Alerting (paging, email) on `failed` jobs.
- Per-org AWS credentials — credentials stay global (one `ProvidersConfig` per system).

## Problem

What's already in place (committed):

- `POST /api/v1/nodes/add/aws` enqueues via `PulumiAWSAdapter.provision_node`, which creates an `asyncio.create_task(_provision_async(...))`. The task runs `stack.up()` in a thread, then writes outputs back to `compute_pools.metadata`.
- `node_provisioning_events` table + cursor-based polling endpoints exist (per [`2026-05-25-aws-node-provisioning-ux.md`](2026-05-25-aws-node-provisioning-ux.md)).
- `InstanceDetail.tsx` has an Overview tab with a `ProvisioningStatus` card; *Logs* and *Shell* tabs work post-bootstrap.
- The wizard has a Normal GPU / Heavy GPU / CPU tier selector (`0783fe6`) that filters the instance-type dropdown client-side.
- Pulumi CLI is installed in the orchestration image (`a475cd7`).
- EC2 stack is torn down on node delete (`2f8fbcd`).

What's broken / missing:

- **The async `_provision_async` task swallows exceptions.** If Pulumi raises (missing CLI, bad creds, AMI lookup fails, AWS API throws), the task dies silently. The `compute_inventory` row stays at `state='provisioning'` forever; no event-log row is written; the dashboard polls and shows nothing.
- **No retries.** A throttled `RequestLimitExceeded` from AWS = the whole provision fails. The operator has no signal that retrying would help.
- **No preflight.** If the Pulumi CLI is absent, AWS creds are wrong, the subnet doesn't exist, or the AMI isn't available in the region, we discover it deep inside `stack.up()` after a long delay, and the error reads as a Pulumi diagnostic blob.
- **No crash recovery.** If `inferia-app` restarts during a provision, the in-flight task is gone. No mechanism resumes it; no mechanism marks it failed.
- **The CPU tier doesn't actually work.** Worker `recipes.go` hard-rejects `len(GPUIndices) == 0` regardless of engine. CPU-only nodes can register but no model can deploy onto them. The selector tab is currently a UI lie. (See `[[project_smoke_worker_gpu_gap]]`.)
- **The Overview tab doesn't surface AWS metadata.** Instance ID, public DNS, region, AMI sit in `compute_pools.metadata` JSONB; the React component doesn't read them.
- **No Retry button.** Operators with a `failed` node delete + recreate, losing the original spec.

## Architecture

The fire-and-forget `_provision_async` model is replaced by a **persisted state machine driven by a reconciler loop**. Postgres is the source of truth; HTTP handlers enqueue rows; a single active reconciler in `inferia-app` claims rows under `FOR UPDATE SKIP LOCKED` and drives them through phases.

```
HTTP POST /api/v1/nodes/add/aws
        │
        ▼
┌─────────────────────────────┐      ┌──────────────────────────────┐
│  Enqueue (sync, fast)       │      │  ProvisioningReconciler      │
│  • Insert provisioning_jobs │      │  (background async task in   │
│  • Insert compute_inventory │      │   inferia-app)               │
│    state='provisioning'     │      │                              │
│  • Return {node_id, job_id} │      │  loop:                       │
└─────────────────────────────┘      │    lease one pending job     │
                                     │    dispatch to phase handler │
                                     │    write outcome → DB +      │
                                     │      event log               │
                                     │    release / renew lease     │
                                     └──────────────┬───────────────┘
                                                    │
                              ┌─────────────────────┼─────────────────────┐
                              ▼                     ▼                     ▼
                       ┌────────────┐       ┌─────────────┐       ┌─────────────┐
                       │ Preflight  │ ───▶  │  PulumiUp   │ ───▶  │ Bootstrap   │
                       │ handler    │       │  handler    │       │  handler    │
                       └────────────┘       └─────────────┘       └─────────────┘
```

**Key properties:**

- HTTP path is thin (~200 ms response). No fire-and-forget task to lose.
- DB is source of truth. Every phase transition + every log line is durably written.
- One active reconciler at a time via a Postgres advisory lock. Multi-replica safe today; becomes leader election for free if scaled out.
- Phase handlers raise typed exceptions; the reconciler classifies and decides retry vs fail.
- Existing `node_provisioning_events` stays as the UI-facing log; the reconciler writes to it; the *Overview* tab's existing polling continues unchanged.

## Data Model

### New table: `provisioning_jobs`

```sql
CREATE TABLE provisioning_jobs (
    id                   UUID PRIMARY KEY,
    node_id              UUID NOT NULL REFERENCES compute_inventory(id) ON DELETE CASCADE,
    pool_id              UUID NOT NULL,
    org_id               TEXT NOT NULL,
    provider             TEXT NOT NULL,                  -- 'aws' today
    spec                 JSONB NOT NULL,                 -- inbound payload from POST /nodes/add/aws

    phase                TEXT NOT NULL,                  -- see state machine below
    attempt_count        INT  NOT NULL DEFAULT 0,
    next_attempt_after   TIMESTAMPTZ,                    -- backoff gate; NULL = run now

    last_error_code      TEXT,                           -- e.g. 'PULUMI_CLI_MISSING'
    last_error_message   TEXT,
    last_error_hint      TEXT,                           -- actionable hint
    error_class          TEXT,                           -- 'TRANSIENT' | 'PERMANENT' | 'INFRASTRUCTURE'

    lease_holder         TEXT,                           -- 'inferia-app-<pid>-<hostname>'
    lease_expires_at     TIMESTAMPTZ,

    pulumi_stack_outputs JSONB,                          -- {instance_id, public_dns, region, ami_id, ...}

    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT phase_check
        CHECK (phase IN ('pending','preflight','provisioning','bootstrapping',
                         'ready','failed','cancelling','terminated'))
);

CREATE INDEX provisioning_jobs_claimable_idx
    ON provisioning_jobs (next_attempt_after NULLS FIRST, updated_at)
    WHERE phase IN ('pending','preflight','provisioning','bootstrapping','cancelling');
```

### `compute_inventory` additions

```sql
ALTER TABLE compute_inventory
    ADD COLUMN instance_class TEXT
        CHECK (instance_class IN ('normal_gpu','heavy_gpu','cpu')),
    ADD COLUMN instance_type  TEXT;
```

Populated by the enqueue path. `instance_class` powers the filter chip group on `Instances.tsx` (deferred to a follow-up, but the column lands now to avoid a second migration). `instance_type` is the literal EC2 type Pulumi uses.

### State machine

```
              ┌─────────┐
              │ pending │  (created by enqueue)
              └────┬────┘
                   ▼
              ┌──────────┐    TRANSIENT err → backoff, stay
              │preflight │ ── PERMANENT err ──┐
              └────┬─────┘                    │
                   ▼                          │
            ┌─────────────┐    TRANSIENT      │
            │provisioning │ ── err → backoff  │
            │  (pulumi up)│                   │
            └─────┬───────┘                   │
                  ▼                           │
          ┌──────────────┐                    │
          │bootstrapping │                    │
          │(worker reg.) │                    │
          └──────┬───────┘                    │
                 ▼                            ▼
             ┌───────┐                  ┌────────┐
             │ ready │ (terminal)       │ failed │ (terminal)
             └───────┘                  └────────┘
                 │                          ▲
                 │     user delete          │
                 └──────────▶ ┌────────────┐ │
                              │ cancelling │─┤
                              └─────┬──────┘ │
                                    ▼        │
                              ┌────────────┐ │
                              │ terminated │─┘
                              └────────────┘
```

| `provisioning_jobs.phase` | `compute_inventory.state` |
|---|---|
| pending, preflight, provisioning, bootstrapping | `provisioning` |
| ready | `ready` |
| failed | `failed` (new enum value — see migration) |
| cancelling, terminated | `terminated` |

**Enum extension.** `compute_inventory.state` is type `node_state` (Postgres enum) with values `provisioning, ready, busy, draining, unhealthy, terminated`. The migration extends it with `failed`:

```sql
-- Must run OUTSIDE a transaction block (Postgres restriction on ALTER TYPE ADD VALUE).
-- The split-file migrator already runs each .sql file in its own connection so
-- DO NOT wrap with BEGIN…COMMIT.
ALTER TYPE node_state ADD VALUE IF NOT EXISTS 'failed';
```

We deliberately do *not* reuse the existing `'unhealthy'` value — that has established semantics ("registered worker stopped heartbeating"; written by `inventory_repo.mark_unhealthy`) which would be muddied by overloading it with provisioning failure.

## Component Boundaries

All new code lives under `package/src/inferia/services/orchestration/services/provisioning/`. The existing `PulumiAWSAdapter` shrinks to a pure "given a spec, run Pulumi up and return outputs" function — no async tasks, no DB writes — invoked from a phase handler.

```
services/orchestration/services/provisioning/
├── __init__.py
├── jobs/
│   ├── model.py            ProvisioningJob (Pydantic), Phase enum, ErrorClass enum
│   ├── repository.py       ProvisioningJobRepository (asyncpg)
│   └── migrations/
│       └── 0NNN_provisioning_jobs.sql
├── reconciler/
│   ├── loop.py             ProvisioningReconciler (background task, lease + dispatch)
│   ├── lease.py            claim_next_job, renew_lease, release_lease
│   └── concurrency.py      WorkerPool (N async workers sharing the loop)
├── phases/
│   ├── base.py             PhaseHandler protocol, PhaseResult dataclass
│   ├── preflight.py        PreflightHandler
│   ├── pulumi_up.py        PulumiUpHandler (thin wrapper over the pruned adapter)
│   ├── bootstrap.py        BootstrapHandler (waits for worker register)
│   └── cancel.py           CancelHandler (pulumi destroy on user delete)
├── retry/
│   ├── classifier.py       classify_error(exc) -> ClassifiedError
│   └── backoff.py          next_attempt_after(attempt, error_class)
├── errors.py               typed exception hierarchy
└── events.py               emit_event(node_id, phase, status, message, extra)
```

### Public interfaces

```python
# phases/base.py
@dataclass
class PhaseResult:
    next_phase: Phase | None        # None = stay in current phase (used for retry)
    outputs: dict | None = None     # merged into provisioning_jobs.pulumi_stack_outputs
    event: EventLine | None = None  # one summary event row to emit on success

class PhaseHandler(Protocol):
    name: Phase
    async def run(self, job: ProvisioningJob, ctx: PhaseContext) -> PhaseResult: ...
```

A handler either returns a `PhaseResult` (success path) or raises — the reconciler classifies the exception via `retry.classifier.classify_error` and writes the outcome. Handlers do not touch the jobs table directly; the reconciler owns all state transitions.

```python
# reconciler/loop.py
class ProvisioningReconciler:
    def __init__(self, repo, handlers: dict[Phase, PhaseHandler],
                 concurrency: int = 4, poll_interval_s: float = 2.0): ...
    async def run(self) -> None: ...                # blocks until cancelled
    async def stop(self) -> None: ...               # graceful drain
```

### Changes to existing code

| File | Change |
|---|---|
| `api/nodes.py::add_provider_node` | Becomes thin enqueue: insert `provisioning_jobs` (`phase='pending'`) + placeholder `compute_inventory` row, return `{node_id, job_id}`. No `asyncio.create_task`. |
| `adapter_engine/adapters/pulumi/pulumi_aws_adapter.py` | `provision_node` / `_provision_async` deleted. Add `run_pulumi_up_sync(spec, env) -> StackOutputs` — sync, pure, no DB writes. Errors propagate as typed exceptions from `errors.py`. |
| `adapter_engine/adapters/pulumi/credentials.py` | Add `verify_credentials(cfg) -> None` doing `sts:GetCallerIdentity`. Used by `PreflightHandler`. |
| `adapter_engine/adapters/aws/bootstrap_builder.py` | Branch on `instance_class`. CPU: skip NVIDIA driver install, set `ALLOCATABLE_GPU_OVERRIDE=0`, don't pass `--gpus all`. GPU classes: existing behavior; `ALLOCATABLE_GPU_OVERRIDE = gpu_count` from catalog row. |
| `adapter_engine/adapters/aws/instance_catalog.py` | **NEW.** Curated catalog of `InstanceType` records grouped by class. |
| `api/nodes.py` (`GET /provisioning`) | Response shape gains `error`, `aws_metadata`, `attempt_count` fields. Read joins `provisioning_jobs` into the existing event-log summary. |
| `api/nodes.py` (`POST /retry`) | **NEW.** Resets `phase='pending'`, clears error fields, `attempt_count=0`, `next_attempt_after=NULL`. 409 if current phase non-terminal. |
| `api/nodes.py` (`DELETE /nodes/{id}`) | Sets `phase='cancelling'` if non-terminal; reconciler runs `CancelHandler` (pulumi destroy), transitions to `terminated`. |
| `api/providers.py::GET /providers/aws/instance-catalog` | **NEW.** Returns the curated catalog grouped by class. |
| `startup_events.py` | Start the reconciler task; advisory-lock on `pg_try_advisory_lock(<const>)` for single-active reconciler across replicas. |
| `inferia-worker/internal/runtime/recipes/recipes.go` | Relax `len(GPUIndices) == 0` rejection for CPU-deployable engines (`ollama`, `infinity`). GPU-only engines (`vllm`, `tgi`) still hard-reject. |

## Instance Class Selector

The wizard already has the tier selector (`0783fe6`). This spec adds the backend semantics so the choice flows through to provisioning and downstream operation.

### Curated catalog (`adapters/aws/instance_catalog.py`)

```python
@dataclass(frozen=True)
class InstanceType:
    name: str
    cls: Literal["normal_gpu", "heavy_gpu", "cpu"]
    vcpu: int
    ram_gb: int
    gpu_count: int          # 0 for cpu
    gpu_model: str | None
    gpu_ram_gb: int         # 0 for cpu
    approx_usd_per_hour: float
```

| Class | Initial instance types | Use case |
|---|---|---|
| `normal_gpu` (default) | `g5.xlarge`, `g5.2xlarge`, `g5.4xlarge`, `g6.xlarge`, `g6.2xlarge`, `g6.4xlarge` | Single-GPU inference (7–13B models, 24 GB VRAM) |
| `heavy_gpu` | `g5.12xlarge`, `g5.48xlarge`, `g6.12xlarge`, `p4d.24xlarge`, `p4de.24xlarge`, `p5.48xlarge` | Multi-GPU / large model inference (70B+, multi-replica) |
| `cpu` | `c6i.xlarge`, `c6i.2xlarge`, `c6i.4xlarge`, `m6i.xlarge`, `m6i.2xlarge`, `m6i.4xlarge` | Quantized small models, embeddings, cheap test pools |

A `GET /api/v1/providers/aws/instance-catalog` endpoint returns the catalog grouped by class.

### Downstream effects

1. **`PreflightHandler`** validates `instance_type ∈ catalog` and `instance_type.cls == spec.instance_class`. Mismatched class/type → `PERMANENT` error `INVALID_INSTANCE_TYPE`.
2. **Pulumi program** picks AMI by class: `normal_gpu` / `heavy_gpu` → latest DLAMI (existing path); `cpu` → plain Ubuntu 22.04 server AMI. Existing `pulumi/ami.py` supports both.
3. **`bootstrap_builder.py`** branches on class as described in the change table above.
4. **`inferia-worker/recipes.go`** relaxes the empty-`GPUIndices` rejection for CPU-deployable engines. GPU-only engines (`vllm`, `tgi`) still hard-reject so a deployer doesn't silently end up on a CPU node that can't run their model.
5. **`Compute Nodes` list filter** (deferred) and **`InstanceDetail` Overview AWS-metadata grid** (in scope) both surface `instance_class`.

## API Surface

| Method | Path | Status |
|---|---|---|
| `POST` | `/api/v1/nodes/add/aws` | **modified** — body adds `instance_class`, `instance_type`; response unchanged |
| `GET` | `/api/v1/nodes/{id}/provisioning` | **modified** — response gains `error`, `aws_metadata`, `attempt_count` |
| `GET` | `/api/v1/nodes/{id}/provisioning-logs?after=<id>` | unchanged |
| `POST` | `/api/v1/nodes/{id}/provisioning/retry` | **new** |
| `DELETE` | `/api/v1/nodes/{id}` | **modified** — enqueues `cancelling` if non-terminal |
| `GET` | `/api/v1/providers/aws/instance-catalog` | **new** |

The `/provisioning` response (new shape):

```jsonc
{
  "node_id": "…",
  "job_id": "…",
  "phase": "provisioning",
  "terminal": false,
  "attempt_count": 1,
  "phases": [
    { "name": "preflight",   "status": "succeeded", "started_at": "…", "ended_at": "…" },
    { "name": "provisioning","status": "running",   "started_at": "…", "ended_at": null }
  ],
  "last_event": { "phase": "provisioning", "status": "log", "message": "creating EC2 instance…" },
  "error": null,
  "aws_metadata": {
    "instance_class": "normal_gpu",
    "instance_type":  "g6.xlarge",
    "region":         "us-east-1",
    "ami_id":         "ami-…",
    "instance_id":    "i-…",
    "public_dns":     "ec2-…"
  }
}
```

`error` is either `null` or `{ code, message, hint, class }` (e.g., `{ code: "PULUMI_CLI_MISSING", message: "Pulumi CLI not installed in inferia-app", hint: "Install with curl -fsSL https://get.pulumi.com | sh", class: "PERMANENT" }`).

## Dashboard Changes

**Wizard (`NewPool.tsx`)**: tier selector already exists. This spec adds:
- Submit payload sends `{ instance_class, instance_type }` alongside existing fields.
- Instance-type dropdown loads from `GET /providers/aws/instance-catalog` (currently a hard-coded constant); cached via TanStack Query.

**`InstanceDetail.tsx` Overview tab** — three subsections, top to bottom:

1. **Provisioning status card** (existing `ProvisioningStatus` component, now reliable):
   - Attempt-count badge when `> 1` (small "attempt 3/5").
   - When `phase === 'failed'`: red banner with `error.message` + `error.hint` + **Retry** button (`POST /provisioning/retry`). Retry button is disabled while in-flight; optimistic UI sets `phase = 'pending'` while the request is pending.
   - When `phase === 'ready'`: green check, total elapsed time.

2. **AWS metadata grid** (new; only shown for `provider === 'aws'`):
   ```
   Instance class:  Normal GPU                Instance ID:  i-0abc…
   Instance type:   g6.xlarge                 Public DNS:   ec2-…compute-1.amazonaws.com
   Region:          us-east-1                 AMI:          ami-… (Deep Learning OSS Nvidia Driver AMI)
   ```
   Fields render `—` while still `null`. `Instance ID` and `Public DNS` get copy-to-clipboard buttons. Renders even before the worker registers, as soon as Pulumi reports outputs.

3. **Node Information grid** (existing): provider, agent_kind, GPU/CPU alloc/total, advertise URL, last heartbeat. Unchanged.

**Polling cadence**: existing adaptive logic in `InstanceDetail.tsx` — 2s while non-terminal, 30s when `ready`, stops on `terminated`. No change.

**Out of scope this spec** (deferred follow-ups):
- `Instances.tsx` filter chips by `instance_class`.
- Live cloud-init / console-log tail on Overview (stays in *Logs* sub-tab).

## Error Handling & Retries

### Layering rule

```
Phase handler                  raises typed exception
        │
        ▼
classify_error(exc)            → ErrorClass + code + message + hint
        │
        ▼
Reconciler                     writes outcome to DB (job + event log + inventory)
        │
        ▼
GET /provisioning              returns {error: {...}, phase, attempt_count}
        │
        ▼
Overview tab                   renders banner + Retry button
```

A handler never decides whether to retry. A handler never writes to the jobs table. The classifier is the single source of truth for retry vs fail.

### Typed exception hierarchy (`errors.py`)

```python
class ProvisioningError(Exception):
    code: str
    hint: str | None = None
    def __init__(self, message: str, *, code: str | None = None, hint: str | None = None): ...

class TransientError(ProvisioningError): ...
class AWSThrottledError(TransientError):       code = "AWS_THROTTLED"
class AWSServerError(TransientError):          code = "AWS_5XX"
class PulumiTransientError(TransientError):    code = "PULUMI_TRANSIENT"
class NetworkError(TransientError):            code = "NETWORK_ERROR"

class PermanentError(ProvisioningError): ...
class PulumiCliMissingError(PermanentError):   code = "PULUMI_CLI_MISSING"
class InvalidCredentialsError(PermanentError): code = "INVALID_CREDENTIALS"
class InvalidSpecError(PermanentError):        code = "INVALID_SPEC"
class InvalidInstanceTypeError(PermanentError):code = "INVALID_INSTANCE_TYPE"
class AMINotFoundError(PermanentError):        code = "AMI_NOT_FOUND"
class SubnetNotFoundError(PermanentError):     code = "SUBNET_NOT_FOUND"
class SecurityGroupNotFoundError(PermanentError):code = "SG_NOT_FOUND"

class InfrastructureError(ProvisioningError): ...
class QuotaExceededError(InfrastructureError):       code = "QUOTA_EXCEEDED"
class CapacityUnavailableError(InfrastructureError): code = "INSUFFICIENT_CAPACITY"
class SubnetExhaustedError(InfrastructureError):     code = "SUBNET_EXHAUSTED"
```

### Classifier (`retry/classifier.py`)

```python
def classify_error(exc: Exception) -> ClassifiedError:
    """Map any exception → (ErrorClass, code, message, hint).

    1. If exc is a ProvisioningError, use its declared class + code.
    2. Otherwise, peek at the type/string to detect botocore.ClientError
       AWS error codes, asyncpg errors, network errors, Pulumi outputs,
       and map them to our typed hierarchy.
    3. Anything still unknown → PermanentError code='UNCLASSIFIED' with
       the full repr in the message — fail loud, never silent.
    """
```

**Botocore mapping table** (excerpt — full table in implementation):

| `ClientError['Error']['Code']` | Mapped to |
|---|---|
| `RequestLimitExceeded`, `Throttling`, `ThrottlingException` | `AWSThrottledError` |
| HTTP 5xx without an error code | `AWSServerError` |
| `AuthFailure`, `UnauthorizedOperation`, `InvalidClientTokenId`, `SignatureDoesNotMatch` | `InvalidCredentialsError` |
| `InvalidAMIID.NotFound` | `AMINotFoundError` |
| `InvalidSubnetID.NotFound` | `SubnetNotFoundError` |
| `InvalidGroup.NotFound` | `SecurityGroupNotFoundError` |
| `VcpuLimitExceeded`, `InstanceLimitExceeded` | `QuotaExceededError` |
| `InsufficientInstanceCapacity` | `CapacityUnavailableError` |
| `InvalidParameterValue` (instance type bad) | `InvalidInstanceTypeError` |

### Backoff (`retry/backoff.py`)

```python
TRANSIENT_MAX_ATTEMPTS = 5
def next_attempt_after(attempt: int, *, now: datetime) -> datetime:
    """Exponential backoff with jitter. attempt is 1-indexed.
       1 → 1-2s, 2 → 2-4s, 3 → 4-8s, 4 → 8-16s, 5 → 16-32s. Capped at 60s."""
    base = min(60, 2 ** attempt)
    jitter = random.uniform(0, base)
    return now + timedelta(seconds=jitter + base / 2)
```

- 5th transient failure escalates to `PermanentError` code `RETRIES_EXHAUSTED`, original error preserved in the hint.
- `PERMANENT` / `INFRASTRUCTURE` → `phase='failed'` on first occurrence, no retry.
- **Retry button click resets `attempt_count` to 0** and clears `next_attempt_after`. User-initiated retry doesn't inherit prior backoff.

### Fail-loud invariant

Every code path the reconciler takes ends with a DB write — either advancing the phase, scheduling a retry, or writing a terminal `failed`. There is no "catch and continue" path that lets a job sit in a non-terminal phase with no scheduled work.

1. **Lease holder dies mid-phase** → 5-min lease TTL expires → another reconciler claims it → handler re-runs (Pulumi `stack.up` is idempotent).
2. **Handler raises uncaught (not a `ProvisioningError`)** → classifier returns `code='UNCLASSIFIED'`, class `PERMANENT`, full `repr(exc) + traceback` in message.
3. **DB write itself fails** → reconciler logs at ERROR + retries 3× with backoff. If that fails, the lease eventually expires and another reconciler picks it up.

### Error → UI rendering

| Code | Class | Banner | Hint shown to operator |
|---|---|---|---|
| `PULUMI_CLI_MISSING` | PERMANENT | "Pulumi CLI not installed" | "Install in the `inferia-app` container: `curl -fsSL https://get.pulumi.com \| sh`" |
| `INVALID_CREDENTIALS` | PERMANENT | "AWS credentials rejected" | "Open Settings → Providers → AWS and re-enter your access key" |
| `AMI_NOT_FOUND` | PERMANENT | "AMI unavailable in region" | "AMI `<id>` doesn't exist in `<region>`. Try `us-east-1` or pick a different AMI." |
| `QUOTA_EXCEEDED` | INFRASTRUCTURE | "AWS quota exceeded" | "Request a quota increase from AWS Support for `<family>` in `<region>`" |
| `INSUFFICIENT_CAPACITY` | INFRASTRUCTURE | "AWS has no capacity right now" | "Try a different AZ, instance type, or wait and retry. Spot instances are especially prone to this." |
| `RETRIES_EXHAUSTED` | PERMANENT | "Gave up after 5 transient failures" | (original error's message + hint) |
| `UNCLASSIFIED` | PERMANENT | "Provisioning failed (uncategorized)" | "This wasn't a known error. The full stack trace is in the Logs tab. Please file a bug." |

## Crash Recovery & Leases

### Single-active reconciler via Postgres advisory lock

```python
RECONCILER_LOCK_KEY = 0xD1F2_4B3E_C7A9_1100  # static const, 64-bit

async def start_reconciler(db):
    while True:
        async with db.acquire() as conn:
            got_lock = await conn.fetchval(
                "SELECT pg_try_advisory_lock($1)", RECONCILER_LOCK_KEY
            )
            if got_lock:
                try:
                    await ProvisioningReconciler(db, handlers).run()
                finally:
                    await conn.fetchval(
                        "SELECT pg_advisory_unlock($1)", RECONCILER_LOCK_KEY
                    )
            else:
                await asyncio.sleep(15)
```

- Session-scoped advisory lock; Postgres releases automatically on connection drop.
- Standby replicas poll every 15 s. Recovery latency on primary crash ≈ 15-30 s.
- One reconciler → one in-process `WorkerPool` of 4 → total parallelism = 4 jobs.

### Lease lifecycle

- Claim query atomically sets `lease_holder` + `lease_expires_at = now() + 5 minutes` under `FOR UPDATE SKIP LOCKED`.
- Lease renewal task runs in an `asyncio.TaskGroup` alongside the handler: every 60 s, `UPDATE provisioning_jobs SET lease_expires_at = now() + interval '5 minutes' WHERE id = $1 AND lease_holder = $2`.
- If renewal `UPDATE` affects 0 rows (lease stolen), the renewer cancels the handler task; the job gets re-leased on the next poll cycle.

### Idempotency

| Phase | Re-run safety |
|---|---|
| `preflight` | Pure read-only checks against AWS API. Safe to repeat. |
| `provisioning` | `pulumi up` on the same stack name is idempotent. Stack name = `<org_id>-<pool_id>-<node_id>` (already deterministic). |
| `bootstrapping` | Polls `compute_inventory.state` waiting for the worker. Re-entering = re-poll. |
| `cancelling` | `pulumi destroy` on the stack. Repeating after partial destroy is safe. |

The `PulumiUpHandler` asserts the stack name matches the job's `node_id` before running `stack.up()`; any drift is logged + classified as `PERMANENT`.

### Cancellation interaction

`DELETE /api/v1/nodes/{id}` while phase is non-terminal:
1. Sets `phase = 'cancelling'`, clears `next_attempt_after`, clears lease.
2. Next reconciler tick claims the job (priority via `phase = 'cancelling'` ORDER preference).
3. `CancelHandler` runs `pulumi destroy` (idempotent), then sets `phase = 'terminated'`, `compute_inventory.state = 'terminated'`.

Deleting while `phase = 'failed'` with a partial AWS instance still triggers `pulumi destroy` to clean up the AWS-side mess.

### Startup migration for upgrade day

The migration lives at `package/src/inferia/infra/schema/migrations/20260527_provisioning_jobs.sql`. It is idempotent and runs against the global schema where `compute_inventory` and the `node_state` enum already live.

```sql
-- Migration 20260527_provisioning_jobs.sql (idempotent)
-- Postgres ≥ 12 allows ALTER TYPE ADD VALUE inside a transaction, but the new
-- value cannot be USED in the same transaction. The migrator must therefore
-- commit between the ALTER TYPE below and the INSERT/UPDATE at the bottom
-- that reference 'failed'. Easiest: the split-file migrator runs each .sql
-- file in autocommit, so we statement-by-statement commit naturally.

ALTER TYPE node_state ADD VALUE IF NOT EXISTS 'failed';

ALTER TABLE compute_inventory
    ADD COLUMN IF NOT EXISTS instance_class TEXT
        CHECK (instance_class IN ('normal_gpu','heavy_gpu','cpu')),
    ADD COLUMN IF NOT EXISTS instance_type  TEXT;

CREATE TABLE IF NOT EXISTS provisioning_jobs ( … );  -- as in Data Model above
CREATE INDEX IF NOT EXISTS provisioning_jobs_claimable_idx ON provisioning_jobs (…);

-- One-time: create jobs in 'failed' for any in-flight inventory rows.
INSERT INTO provisioning_jobs (id, node_id, pool_id, org_id, provider, spec,
                               phase, last_error_code, last_error_message,
                               last_error_hint, error_class, attempt_count,
                               created_at, updated_at)
SELECT gen_random_uuid(), ci.id, ci.pool_id, ci.org_id, 'aws', '{}'::jsonb,
       'failed', 'UPGRADE_ABANDONED',
       'This node was provisioned by an older version. State was lost on upgrade. Click Retry to start fresh.',
       'Click Retry', 'PERMANENT', 0, now(), now()
FROM compute_inventory ci
WHERE ci.state = 'provisioning'
  AND ci.agent_kind = 'worker'
  AND NOT EXISTS (SELECT 1 FROM provisioning_jobs pj WHERE pj.node_id = ci.id);

UPDATE compute_inventory SET state = 'failed'
WHERE state = 'provisioning'
  AND agent_kind = 'worker'
  AND id IN (SELECT node_id FROM provisioning_jobs WHERE last_error_code = 'UPGRADE_ABANDONED');
```

Retry button on an `UPGRADE_ABANDONED` job is disabled (spec is empty); operator deletes + recreates from the wizard.

### Shutdown behavior

On `SIGTERM`:
1. Reconciler stops accepting new jobs.
2. In-flight handlers get 30 s grace.
3. After grace, handlers are cancelled — leases stay set, next reconciler picks them up within ≤ 5 min.

## Testing Strategy

Per global rule: ≥ 95 % coverage, edge cases for every public surface, fail-loud invariants asserted.

### Coverage targets

| Layer | Bar |
|---|---|
| `errors.py` + `retry/classifier.py` | **100 %** |
| `retry/backoff.py` | **100 %** |
| `jobs/repository.py` | **≥ 98 %** |
| `reconciler/loop.py` + `lease.py` | **≥ 98 %** |
| `phases/*.py` | **≥ 95 %** |
| `adapters/aws/instance_catalog.py` | **100 %** |
| `adapters/pulumi/pulumi_aws_adapter.py` | **≥ 90 %** |
| New HTTP handlers | **100 %** + real-server integration test per [[feedback_wire_handlers_check]] |
| Dashboard components | **≥ 90 %** + 1-2 Playwright e2e flows |

### Test files

```
package/src/inferia/services/orchestration/services/provisioning/
├── jobs/tests/
│   ├── test_model.py                        Pydantic round-trip, enum values, terminal-vs-non-terminal classification
│   ├── test_repository.py                   enqueue, claim_next_job, renew_lease, release_lease, retry, cancel, transitions
│   └── test_concurrency.py                  20 workers racing claim_next_job hit FOR UPDATE SKIP LOCKED; no double-claim
├── reconciler/tests/
│   ├── test_loop.py                         tick → dispatch → write outcome (mocked handlers, real repo)
│   ├── test_lease.py                        renewal, expiry, takeover, stolen-lease detection
│   ├── test_concurrency.py                  WorkerPool of 4 processes 4 jobs in parallel
│   └── test_shutdown.py                     SIGTERM → drain → cancel after 30s
├── phases/tests/
│   ├── test_preflight.py                    creds OK / creds bad / pulumi missing / subnet missing / SG missing / AMI missing / instance type invalid
│   ├── test_pulumi_up.py                    success → outputs; throttled → TransientError; auth → InvalidCredentials; partial state → resume
│   ├── test_bootstrap.py                    worker registers within timeout; never registers → TransientError until cap
│   └── test_cancel.py                       destroy success; destroy on no-state = no-op
├── retry/tests/
│   ├── test_classifier.py                   every typed exception → expected class+code; every botocore code → mapped; unknown → UNCLASSIFIED
│   └── test_backoff.py                      monotonic increase, cap at 60s, jitter bounds, exhaustion at attempt 5
└── tests/integration/
    ├── test_provision_flow_happy_path.py    POST add/aws → poll /provisioning → ready (moto + Pulumi local + real PG)
    ├── test_provision_flow_retry.py         phase=failed → POST /retry → ready
    ├── test_provision_flow_cancel.py        DELETE mid-provision → terminated, AWS resources cleaned
    ├── test_provision_flow_crash_recovery.py kill reconciler mid-pulumi-up; restart; job completes
    └── test_provision_flow_upgrade.py       apply migration, verify in-flight rows become failed+retryable
```

### Edge cases mandatory to cover

**State machine:** All 7 non-terminal → terminal transitions; illegal transitions raise; concurrent updates with stale phase guarded by `WHERE phase = $expected_old`; `attempt_count` cap at 5 then `RETRIES_EXHAUSTED`.

**Repository:** Empty queue → None; all jobs leased → None; `next_attempt_after` in future → skipped; two callers race on same job → only one gets it (SKIP LOCKED); writes under torn connection retry 3× then raise.

**Lease:** Renew when not holder → returns False, no update; renew when expired but still holder → allowed; release on different holder's job → no-op.

**Classifier:** Every typed exception class tested; every botocore code in table tested; unknown botocore code → `UNCLASSIFIED PERMANENT`; `asyncio.CancelledError` propagates (NOT classified as failure); `KeyboardInterrupt` propagates.

**Backoff:** attempt=1 bounded `[0.5, 2.0]s`; attempt=10 (overflow) still capped at 60s; jitter is non-zero (statistical: 100 samples cover > half the range).

**Phase handlers:** Each of 8 preflight checks fails independently → matching `PermanentError`; deterministic stack name verified on `pulumi_up`; `pulumi` binary missing → `PulumiCliMissingError` with hint; `bootstrap` poll interval respected, gives up after `bootstrap_timeout_seconds` (default 600 s) → `TransientError`; `cancel` on already-destroyed stack → no-op success.

**HTTP:** Invalid `instance_class` → 422; mismatched class/type → 422; retry on non-terminal → 409; retry on terminal → 200 + requeue; retry on missing node → 404; DELETE on non-terminal → cancellation enqueue; DELETE on missing node → 404; DELETE on already-terminated node → 204 (idempotent); GET on missing node → 404; catalog endpoint shape stable across calls.

**Integration:** Happy path < 60 s wall clock under moto + Pulumi local; SIGKILL the worker pool mid-`pulumi up`, restart, job completes (test uses 10 s lease TTL override); Retry on `PULUMI_CLI_MISSING` with binary now installed → success.

**Dashboard (RTL):** Wizard default tab = Normal GPU; tab switch updates instance list; submit payload matches; catalog loading shows skeleton; catalog error shows toast; Overview AWS metadata grid shows em-dash placeholders before outputs land; Retry button disabled while in-flight; `error.hint` text visible when present.

**Playwright e2e:** Happy: configure AWS creds → wizard → submit → wait for `ready` (moto) → smoke chat. Failure: configure AWS creds incorrectly → wizard → submit → Overview shows `INVALID_CREDENTIALS` banner with right hint.

### Fixtures

- `aws_mock` — moto session injected for test scope.
- `pulumi_local_backend` — temp `~/.pulumi`, cleaned per-test.
- `fake_clock` — for backoff + lease-expiry; reconciler accepts injected `time_source`.
- `db_fixture` — installs the migration before each test.
- `dashboard_msw` — Mock Service Worker handlers for every new endpoint.

## Cross-Repo Changes

This spec touches two repositories:

1. **InferiaLLM (`main` branch)** — all of `package/`, `apps/dashboard/`, `docs/specs/`, migrations.
2. **inferia-worker (`feat/aws-ec2-bootstrap` branch)** — `internal/runtime/recipes/recipes.go` relaxation for CPU-deployable engines. One commit, signed with `id_ed25519_gh`, no Claude attribution (per [[feedback_signed_commits]]).

## Risks

- **Pulumi state file persistence.** The Pulumi local backend stores state in `~/.pulumi`. If `inferia-app` runs in a container without a persistent volume mount for that path, restarting the container loses Pulumi state, and `stack.up()` can no longer reconcile. Mitigation: the container image needs `~/.pulumi` mounted on a docker volume; verified in the integration test fixture and called out in deployment docs.
- **AWS creds visibility under the reconciler.** Credentials live in `system_settings.provider_configs` Fernet-encrypted. The reconciler reads via `load_providers_config()` → `resolve_aws_env()` → passes env vars into `local_workspace_opts(env_vars=...)`. Existing path; the refactor doesn't change credential flow.
- **Lease TTL too short.** A real `pulumi up` for a new VPC + subnet + SG + EC2 can take 90-180 s. 5-min TTL with 60-s renewal cadence has 4 renewal cycles of margin. If a phase exceeds 5 min without renewing (e.g., the renewer task itself stalls), the job gets re-claimed and run again — safe because Pulumi is idempotent.
- **Upgrade migration strands in-flight nodes.** Old `_provision_async` tasks are gone after the refactor deploys. We mark them all `failed` (`UPGRADE_ABANDONED`) and ask the operator to delete + recreate. Acceptable trade-off per Section 6 discussion.
- **`UNCLASSIFIED` errors are loud but unactionable for the operator.** They surface the full Python traceback. This is intentional — fail-loud beats silent — but it means the first deployment in a new environment may produce uglier-than-ideal error UIs. Mitigation: a follow-up sweep adds entries to the classifier as `UNCLASSIFIED` cases are seen in production.

## Rollout

1. Land the migration. Existing in-flight rows go to `failed/UPGRADE_ABANDONED`. Operator notified via release notes.
2. Land the backend reconciler + phase handlers + HTTP changes behind no feature flag — the old `_provision_async` code is deleted in the same commit (no shim period; bigger blast radius but cleaner reasoning).
3. Land the worker `recipes.go` relax on `feat/aws-ec2-bootstrap`. Rebuild worker image. Smoke against CPU + GPU pools.
4. Land the frontend changes (Retry button, AWS metadata grid, catalog endpoint integration).
5. Run the integration test suite + Playwright e2e against a staging stack with real (sandbox) AWS creds.

## Open Questions

None remaining as of design approval. All clarifying questions resolved during brainstorming.

## References

- [`2026-05-25-aws-node-provisioning-ux.md`](2026-05-25-aws-node-provisioning-ux.md) — UX spec for `node_provisioning_events`, `/provisioning` endpoints, `ProvisioningStatus` component, Logs and Shell tabs. This spec replaces the backend `_provision_async` path that doc described.
- [`2026-05-22-aws-pulumi.md`](2026-05-22-aws-pulumi.md) — Pulumi adapter design (existing).
- [`2026-05-20-aws-ec2-worker-provisioning.md`](2026-05-20-aws-ec2-worker-provisioning.md) — EC2 worker provisioning (existing).
- Memory: [[project_smoke_qwen3_orchestration_fixes]] (closed bugs), [[project_smoke_worker_gpu_gap]] (CPU recipe gap closed by this spec), [[feedback_pulumi_python_sdk_sync]], [[feedback_pulumi_cli_binary_required]], [[feedback_adapter_singleton_race]], [[feedback_wire_handlers_check]], [[feedback_signed_commits]].

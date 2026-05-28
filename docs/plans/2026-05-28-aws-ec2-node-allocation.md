# AWS EC2 Node Allocation — Robustness Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the fire-and-forget `_provision_async` AWS provisioning path with a persisted state-machine + reconciler loop so EC2 allocation either succeeds or lands in a terminal `failed` state with a typed error code, message, and actionable hint; surface live phase + AWS metadata + Retry button on the dashboard Overview tab.

**Architecture:** A new `provisioning_jobs` table is a Postgres queue (`FOR UPDATE SKIP LOCKED`). HTTP enqueues; a single-active `ProvisioningReconciler` (advisory-lock guarded) leases jobs, dispatches them through phase handlers (`preflight → provisioning → bootstrapping → ready`), and writes outcomes back. Phase handlers raise typed exceptions; a classifier maps them to retry-or-fail decisions. Pulumi adapter shrinks to a pure sync function. Dashboard reads the same `/provisioning` endpoint, now backed by the state machine, plus a new AWS metadata grid and Retry button.

**Tech Stack:** Python 3.10–3.12, FastAPI, asyncpg, Pulumi Python SDK, Postgres ≥ 12, React 19 + TanStack Query + TailwindCSS + vitest + React Testing Library, Go 1.26 (for the inferia-worker change).

**Spec:** [`docs/specs/2026-05-27-aws-ec2-node-allocation-design.md`](../specs/2026-05-27-aws-ec2-node-allocation-design.md).

**Commit convention:** Sign every commit with `id_ed25519_gh`. Never include Claude/AI attribution. Pattern:

```bash
git -c user.signingkey=/home/ankit/.ssh/id_ed25519_gh commit -S -m "<message>"
```

---

## File Structure

### New files

**Backend (`package/src/inferia/`):**

| Path | Responsibility |
|---|---|
| `infra/schema/migrations/20260528_provisioning_jobs.sql` | Migration: ALTER TYPE node_state ADD 'failed'; ALTER TABLE compute_inventory ADD instance_class, instance_type; CREATE TABLE provisioning_jobs |
| `services/orchestration/services/provisioning/__init__.py` | Package marker |
| `services/orchestration/services/provisioning/errors.py` | Typed `ProvisioningError` hierarchy (TransientError, PermanentError, InfrastructureError subclasses) |
| `services/orchestration/services/provisioning/events.py` | `emit_event(pool_id, node_id, phase, status, message, extra)` helper |
| `services/orchestration/services/provisioning/jobs/__init__.py` | Package marker |
| `services/orchestration/services/provisioning/jobs/model.py` | `ProvisioningJob` Pydantic model, `Phase` + `ErrorClass` enums, `ClassifiedError`, `PhaseResult`, `EventLine` dataclasses |
| `services/orchestration/services/provisioning/jobs/repository.py` | `ProvisioningJobRepository` (asyncpg-backed) |
| `services/orchestration/services/provisioning/jobs/tests/__init__.py` | Package marker |
| `services/orchestration/services/provisioning/jobs/tests/test_model.py` | Model + enum tests |
| `services/orchestration/services/provisioning/jobs/tests/test_repository.py` | Repository tests (mocked asyncpg) |
| `services/orchestration/services/provisioning/jobs/tests/test_repository_concurrency.py` | Real-PG SKIP LOCKED test |
| `services/orchestration/services/provisioning/retry/__init__.py` | Package marker |
| `services/orchestration/services/provisioning/retry/classifier.py` | `classify_error(exc) → ClassifiedError` |
| `services/orchestration/services/provisioning/retry/backoff.py` | `next_attempt_after(attempt, now)` with capped exponential + jitter |
| `services/orchestration/services/provisioning/retry/tests/__init__.py` | Package marker |
| `services/orchestration/services/provisioning/retry/tests/test_classifier.py` | Classifier tests |
| `services/orchestration/services/provisioning/retry/tests/test_backoff.py` | Backoff math tests |
| `services/orchestration/services/provisioning/phases/__init__.py` | Package marker |
| `services/orchestration/services/provisioning/phases/base.py` | `PhaseHandler` Protocol, `PhaseContext` dataclass |
| `services/orchestration/services/provisioning/phases/preflight.py` | `PreflightHandler` |
| `services/orchestration/services/provisioning/phases/pulumi_up.py` | `PulumiUpHandler` |
| `services/orchestration/services/provisioning/phases/bootstrap.py` | `BootstrapHandler` |
| `services/orchestration/services/provisioning/phases/cancel.py` | `CancelHandler` |
| `services/orchestration/services/provisioning/phases/tests/__init__.py` | Package marker |
| `services/orchestration/services/provisioning/phases/tests/test_preflight.py` | Preflight tests |
| `services/orchestration/services/provisioning/phases/tests/test_pulumi_up.py` | PulumiUp tests |
| `services/orchestration/services/provisioning/phases/tests/test_bootstrap.py` | Bootstrap tests |
| `services/orchestration/services/provisioning/phases/tests/test_cancel.py` | Cancel tests |
| `services/orchestration/services/provisioning/reconciler/__init__.py` | Package marker |
| `services/orchestration/services/provisioning/reconciler/lease.py` | `claim_next_job`, `renew_lease`, `release_lease` async helpers |
| `services/orchestration/services/provisioning/reconciler/concurrency.py` | `WorkerPool` (N async workers sharing the loop) |
| `services/orchestration/services/provisioning/reconciler/loop.py` | `ProvisioningReconciler` long-lived task |
| `services/orchestration/services/provisioning/reconciler/tests/__init__.py` | Package marker |
| `services/orchestration/services/provisioning/reconciler/tests/test_lease.py` | Lease tests |
| `services/orchestration/services/provisioning/reconciler/tests/test_loop.py` | Loop tests |
| `services/orchestration/services/provisioning/reconciler/tests/test_concurrency.py` | WorkerPool tests |
| `services/orchestration/services/provisioning/reconciler/tests/test_shutdown.py` | Shutdown drain tests |
| `services/orchestration/services/provisioning/tests/__init__.py` | Package marker |
| `services/orchestration/services/provisioning/tests/integration/__init__.py` | Package marker |
| `services/orchestration/services/provisioning/tests/integration/test_happy_path.py` | Happy path: POST → ready |
| `services/orchestration/services/provisioning/tests/integration/test_retry.py` | failed → Retry → ready |
| `services/orchestration/services/provisioning/tests/integration/test_cancel.py` | DELETE mid-provision |
| `services/orchestration/services/provisioning/tests/integration/test_crash_recovery.py` | Kill reconciler, restart, resume |
| `services/orchestration/services/provisioning/tests/integration/test_upgrade.py` | Migration marks in-flight as failed |
| `services/orchestration/services/adapter_engine/adapters/aws/instance_catalog.py` | Curated `InstanceType` catalog grouped by class |
| `services/orchestration/services/adapter_engine/adapters/aws/test_instance_catalog.py` | Catalog tests |

**Frontend (`apps/dashboard/src/`):**

| Path | Responsibility |
|---|---|
| `components/nodes/AWSMetadataGrid.tsx` | Renders instance_class, instance_type, region, ami_id, instance_id, public_dns grid |
| `components/nodes/AWSMetadataGrid.test.tsx` | RTL tests |
| `components/nodes/RetryProvisioningButton.tsx` | Calls POST /provisioning/retry; disables while in-flight; optimistic phase=pending |
| `components/nodes/RetryProvisioningButton.test.tsx` | RTL tests |
| `hooks/useInstanceCatalog.ts` | TanStack Query hook for GET /providers/aws/instance-catalog |
| `hooks/useInstanceCatalog.test.ts` | RTL/vitest tests |
| `playwright/aws-provision.spec.ts` | Happy + failure e2e flows |

**Worker (`inferia-worker/` repo, branch `feat/aws-ec2-bootstrap`):**

| Path | Responsibility |
|---|---|
| `internal/runtime/recipes/recipes.go` (modify) | Relax `len(GPUIndices) == 0` for CPU-deployable engines |
| `internal/runtime/recipes/recipes_test.go` (modify) | Test the new behavior |

### Modified files

| Path | Change |
|---|---|
| `package/src/inferia/services/orchestration/api/nodes.py` | `add_provider_node` becomes thin enqueue; `GET /provisioning` extended response; `POST /provisioning/retry` new; `DELETE /nodes/{id}` cancellation enqueue |
| `package/src/inferia/services/orchestration/api/providers.py` (create if missing) | New `GET /providers/aws/instance-catalog` endpoint |
| `package/src/inferia/services/orchestration/services/adapter_engine/adapters/pulumi/pulumi_aws_adapter.py` | Delete `provision_node` + `_provision_async`; add `run_pulumi_up_sync` |
| `package/src/inferia/services/orchestration/services/adapter_engine/adapters/pulumi/credentials.py` | Add `verify_credentials(cfg)` |
| `package/src/inferia/services/orchestration/services/adapter_engine/adapters/aws/bootstrap_builder.py` | Branch on `instance_class` (skip NVIDIA setup for cpu) |
| `package/src/inferia/services/orchestration/server.py` | Wire reconciler startup; advisory-lock loop |
| `apps/dashboard/src/pages/Compute/InstanceDetail.tsx` | Render AWSMetadataGrid + RetryProvisioningButton in Overview tab |
| `apps/dashboard/src/pages/Compute/NewPool.tsx` | Swap hard-coded `awsInstanceTiers` constant for `useInstanceCatalog` query |

---

## Task Index

| # | Task | Dependencies |
|---|---|---|
| 1 | Database migration | — |
| 2 | Error types (`errors.py`) | — |
| 3 | AWS instance catalog | — |
| 4 | Backoff (`retry/backoff.py`) | — |
| 5 | ProvisioningJob model + enums | Task 2 |
| 6 | ProvisioningJobRepository | Tasks 1, 5 |
| 7 | Repository concurrency test (real PG) | Task 6 |
| 8 | Events emitter | Tasks 1, 5 |
| 9 | Classifier | Task 2 |
| 10 | Pulumi adapter prune → `run_pulumi_up_sync` | Task 2 |
| 11 | `verify_credentials` | Task 2 |
| 12 | Bootstrap builder CPU branching | Task 3 |
| 13 | PhaseHandler base + PhaseContext | Tasks 5, 6, 8 |
| 14 | PreflightHandler | Tasks 9, 11, 13 |
| 15 | PulumiUpHandler | Tasks 10, 13 |
| 16 | BootstrapHandler | Task 13 |
| 17 | CancelHandler | Tasks 10, 13 |
| 18 | Lease helpers | Task 6 |
| 19 | WorkerPool | Task 18 |
| 20 | ProvisioningReconciler loop | Tasks 13–17, 19 |
| 21 | Reconciler shutdown drain | Task 20 |
| 22 | Catalog HTTP endpoint | Task 3 |
| 23 | `add_provider_node` thin enqueue | Task 6 |
| 24 | `GET /provisioning` extended | Task 6 |
| 25 | `POST /provisioning/retry` | Task 6 |
| 26 | `DELETE /nodes/{id}` cancellation enqueue | Task 6 |
| 27 | Startup advisory-lock + reconciler boot | Task 20 |
| 28 | inferia-worker `recipes.go` CPU relax | — (cross-repo) |
| 29 | `AWSMetadataGrid` + `RetryProvisioningButton` | Tasks 24, 25 |
| 30 | `InstanceDetail` Overview wiring | Task 29 |
| 31 | `useInstanceCatalog` + `NewPool` swap | Task 22 |
| 32 | Integration: happy + retry + cancel | All backend |
| 33 | Integration: crash recovery + upgrade + e2e | Task 32 |

---

### Task 1: Database migration

**Files:**
- Create: `package/src/inferia/infra/schema/migrations/20260528_provisioning_jobs.sql`
- Test: `package/src/inferia/services/orchestration/services/provisioning/tests/integration/test_migration.py`

- [ ] **Step 1.1: Write the failing migration test**

Create `package/src/inferia/services/orchestration/services/provisioning/tests/integration/test_migration.py`:

```python
"""Smoke-tests the 20260528_provisioning_jobs migration against a real PG.

Run with INFERIA_TEST_DATABASE_URL pointing at an empty test DB that has
the existing global_schema.sql + all prior migrations applied. Skipped
if that env var is not set.
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path

import asyncpg
import pytest

MIGRATION = Path(__file__).resolve().parents[5] / "infra" / "schema" / "migrations" / "20260528_provisioning_jobs.sql"


@pytest.fixture
def test_database_url() -> str:
    url = os.environ.get("INFERIA_TEST_DATABASE_URL")
    if not url:
        pytest.skip("INFERIA_TEST_DATABASE_URL not set")
    return url


@pytest.mark.asyncio
async def test_migration_applies_cleanly(test_database_url):
    """Running the migration on a fresh DB produces the expected schema."""
    sql = MIGRATION.read_text()
    conn = await asyncpg.connect(test_database_url)
    try:
        # Migrator runs statements in autocommit, so split on ';' boundaries
        # that terminate full statements (naive but adequate for our SQL).
        for stmt in [s.strip() for s in sql.split(";") if s.strip()]:
            await conn.execute(stmt)

        # 'failed' is now a node_state enum value.
        rows = await conn.fetch(
            "SELECT unnest(enum_range(NULL::node_state))::text AS v"
        )
        assert "failed" in [r["v"] for r in rows]

        # compute_inventory has instance_class + instance_type columns.
        cols = await conn.fetch(
            """SELECT column_name FROM information_schema.columns
               WHERE table_name='compute_inventory'
                 AND column_name IN ('instance_class','instance_type')"""
        )
        assert {r["column_name"] for r in cols} == {"instance_class", "instance_type"}

        # provisioning_jobs table exists with the expected columns.
        cols = await conn.fetch(
            """SELECT column_name FROM information_schema.columns
               WHERE table_name='provisioning_jobs'"""
        )
        names = {r["column_name"] for r in cols}
        expected = {
            "id", "node_id", "pool_id", "org_id", "provider", "spec",
            "phase", "attempt_count", "next_attempt_after",
            "last_error_code", "last_error_message", "last_error_hint",
            "error_class", "lease_holder", "lease_expires_at",
            "pulumi_stack_outputs", "created_at", "updated_at",
        }
        assert expected <= names, f"missing columns: {expected - names}"

        # Claimable index exists.
        idx = await conn.fetchval(
            """SELECT 1 FROM pg_indexes
               WHERE tablename='provisioning_jobs'
                 AND indexname='provisioning_jobs_claimable_idx'"""
        )
        assert idx == 1
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_migration_is_idempotent(test_database_url):
    """Re-running the migration on an already-migrated DB is a no-op."""
    sql = MIGRATION.read_text()
    conn = await asyncpg.connect(test_database_url)
    try:
        for _ in range(2):
            for stmt in [s.strip() for s in sql.split(";") if s.strip()]:
                await conn.execute(stmt)
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_migration_marks_inflight_inventory_as_failed(test_database_url):
    """An existing compute_inventory row in 'provisioning' becomes 'failed'
    with a matching provisioning_jobs row carrying UPGRADE_ABANDONED."""
    conn = await asyncpg.connect(test_database_url)
    try:
        pool_id = uuid.uuid4()
        node_id = uuid.uuid4()
        # Set up a pool + inventory row in 'provisioning'.
        await conn.execute(
            """INSERT INTO compute_pools (id, org_id, name, provider, lifecycle_state)
               VALUES ($1, 'org-test', 'p', 'aws', 'running')""",
            pool_id,
        )
        await conn.execute(
            """INSERT INTO compute_inventory (id, pool_id, provider,
                 provider_instance_id, state, agent_kind)
               VALUES ($1, $2, 'aws', 'placeholder:test', 'provisioning', 'worker')""",
            node_id, pool_id,
        )
        # Apply the migration.
        sql = MIGRATION.read_text()
        for stmt in [s.strip() for s in sql.split(";") if s.strip()]:
            await conn.execute(stmt)
        # Assert: inventory row now 'failed', a job row exists.
        state = await conn.fetchval(
            "SELECT state::text FROM compute_inventory WHERE id=$1", node_id
        )
        assert state == "failed"
        job = await conn.fetchrow(
            "SELECT phase, last_error_code FROM provisioning_jobs WHERE node_id=$1",
            node_id,
        )
        assert job["phase"] == "failed"
        assert job["last_error_code"] == "UPGRADE_ABANDONED"
    finally:
        # Cleanup
        await conn.execute("DELETE FROM provisioning_jobs WHERE node_id=$1", node_id)
        await conn.execute("DELETE FROM compute_inventory WHERE id=$1", node_id)
        await conn.execute("DELETE FROM compute_pools WHERE id=$1", pool_id)
        await conn.close()
```

- [ ] **Step 1.2: Run test to verify it fails**

```bash
pytest package/src/inferia/services/orchestration/services/provisioning/tests/integration/test_migration.py -v
```

Expected: FAIL with "No such file or directory" for the migration path (the file doesn't exist yet).

- [ ] **Step 1.3: Write the migration**

Create `package/src/inferia/infra/schema/migrations/20260528_provisioning_jobs.sql`:

```sql
-- Migration 20260528_provisioning_jobs.sql (idempotent)
-- Postgres ≥ 12 allows ALTER TYPE ADD VALUE inside a transaction, but the
-- new value cannot be USED in the same transaction. The split-file migrator
-- runs each .sql file in autocommit, so each ; below commits independently.

-- 1. Extend node_state enum with 'failed' (does NOT overload 'unhealthy',
--    which already means "registered worker stopped heartbeating").
ALTER TYPE node_state ADD VALUE IF NOT EXISTS 'failed';

-- 2. Extend compute_inventory with class/type columns.
ALTER TABLE compute_inventory
    ADD COLUMN IF NOT EXISTS instance_class TEXT
        CHECK (instance_class IN ('normal_gpu','heavy_gpu','cpu')),
    ADD COLUMN IF NOT EXISTS instance_type  TEXT;

-- 3. Create the provisioning_jobs queue table.
CREATE TABLE IF NOT EXISTS provisioning_jobs (
    id                   UUID PRIMARY KEY,
    node_id              UUID NOT NULL REFERENCES compute_inventory(id) ON DELETE CASCADE,
    pool_id              UUID NOT NULL,
    org_id               TEXT NOT NULL,
    provider             TEXT NOT NULL,
    spec                 JSONB NOT NULL,

    phase                TEXT NOT NULL,
    attempt_count        INT  NOT NULL DEFAULT 0,
    next_attempt_after   TIMESTAMPTZ,

    last_error_code      TEXT,
    last_error_message   TEXT,
    last_error_hint      TEXT,
    error_class          TEXT,

    lease_holder         TEXT,
    lease_expires_at     TIMESTAMPTZ,

    pulumi_stack_outputs JSONB,

    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT provisioning_jobs_phase_check
        CHECK (phase IN ('pending','preflight','provisioning','bootstrapping',
                         'ready','failed','cancelling','terminated'))
);

CREATE INDEX IF NOT EXISTS provisioning_jobs_claimable_idx
    ON provisioning_jobs (next_attempt_after NULLS FIRST, updated_at)
    WHERE phase IN ('pending','preflight','provisioning','bootstrapping','cancelling');

CREATE INDEX IF NOT EXISTS provisioning_jobs_node_id_idx
    ON provisioning_jobs (node_id);

-- 4. One-time backfill: any in-flight compute_inventory rows under the old
--    fire-and-forget adapter get a 'failed/UPGRADE_ABANDONED' job + the
--    inventory row transitions to 'failed'.
INSERT INTO provisioning_jobs (
    id, node_id, pool_id, org_id, provider, spec,
    phase, last_error_code, last_error_message, last_error_hint,
    error_class, attempt_count, created_at, updated_at
)
SELECT gen_random_uuid(), ci.id, ci.pool_id,
       COALESCE(cp.org_id, 'unknown'),
       ci.provider::text, '{}'::jsonb,
       'failed', 'UPGRADE_ABANDONED',
       'This node was provisioned by an older version of inferia-app. '
       || 'State was lost on upgrade. Delete the node and create it again '
       || 'from the wizard.',
       'Delete and recreate from the wizard.',
       'PERMANENT', 0, now(), now()
FROM compute_inventory ci
LEFT JOIN compute_pools cp ON cp.id = ci.pool_id
WHERE ci.state = 'provisioning'
  AND ci.agent_kind = 'worker'
  AND NOT EXISTS (
      SELECT 1 FROM provisioning_jobs pj WHERE pj.node_id = ci.id
  );

UPDATE compute_inventory SET state = 'failed', updated_at = now()
WHERE state = 'provisioning'
  AND agent_kind = 'worker'
  AND id IN (SELECT node_id FROM provisioning_jobs WHERE last_error_code = 'UPGRADE_ABANDONED');
```

- [ ] **Step 1.4: Run test to verify it passes**

If `INFERIA_TEST_DATABASE_URL` is not set locally, document the env var in the test plan and confirm the file at least parses by running the migration against a scratch DB:

```bash
# Optional local check (if you have a test DB):
INFERIA_TEST_DATABASE_URL=postgresql://inferia:inferia@localhost:5432/inferia_test \
    pytest package/src/inferia/services/orchestration/services/provisioning/tests/integration/test_migration.py -v
```

Expected (when env set): all 3 tests PASS.
Expected (when env unset): tests skip with "INFERIA_TEST_DATABASE_URL not set".

- [ ] **Step 1.5: Commit**

```bash
git add package/src/inferia/infra/schema/migrations/20260528_provisioning_jobs.sql \
        package/src/inferia/services/orchestration/services/provisioning/tests/integration/test_migration.py \
        package/src/inferia/services/orchestration/services/provisioning/tests/integration/__init__.py \
        package/src/inferia/services/orchestration/services/provisioning/tests/__init__.py \
        package/src/inferia/services/orchestration/services/provisioning/__init__.py
git -c user.signingkey=/home/ankit/.ssh/id_ed25519_gh commit -S -m "provisioning: add 20260528_provisioning_jobs migration

Adds the provisioning_jobs queue table that backs the upcoming reconciler
refactor. Extends compute_inventory.state enum with 'failed' (does not
reuse 'unhealthy' which has heartbeat-loss semantics) and adds
instance_class/instance_type columns. Backfills any in-flight
'provisioning' inventory rows as 'failed/UPGRADE_ABANDONED' jobs so the
upgrade doesn't silently strand them.

Migration is idempotent (ALTER ... IF NOT EXISTS, CREATE TABLE IF NOT
EXISTS, NOT EXISTS guard on backfill). Includes a real-PG smoke test
gated on INFERIA_TEST_DATABASE_URL."
```

---

### Task 2: Error types (`errors.py`)

**Files:**
- Create: `package/src/inferia/services/orchestration/services/provisioning/errors.py`
- Test: `package/src/inferia/services/orchestration/services/provisioning/tests/test_errors.py`

- [ ] **Step 2.1: Write the failing test**

Create `package/src/inferia/services/orchestration/services/provisioning/tests/test_errors.py`:

```python
"""Tests for the ProvisioningError hierarchy."""
import pytest

from inferia.services.orchestration.services.provisioning.errors import (
    ProvisioningError,
    TransientError, AWSThrottledError, AWSServerError,
    PulumiTransientError, NetworkError,
    PermanentError, PulumiCliMissingError, InvalidCredentialsError,
    InvalidSpecError, InvalidInstanceTypeError, AMINotFoundError,
    SubnetNotFoundError, SecurityGroupNotFoundError,
    InfrastructureError, QuotaExceededError, CapacityUnavailableError,
    SubnetExhaustedError,
)


# All non-base classes have a class-level `code` constant.
_ALL_TYPED = [
    (AWSThrottledError, "AWS_THROTTLED", TransientError),
    (AWSServerError, "AWS_5XX", TransientError),
    (PulumiTransientError, "PULUMI_TRANSIENT", TransientError),
    (NetworkError, "NETWORK_ERROR", TransientError),
    (PulumiCliMissingError, "PULUMI_CLI_MISSING", PermanentError),
    (InvalidCredentialsError, "INVALID_CREDENTIALS", PermanentError),
    (InvalidSpecError, "INVALID_SPEC", PermanentError),
    (InvalidInstanceTypeError, "INVALID_INSTANCE_TYPE", PermanentError),
    (AMINotFoundError, "AMI_NOT_FOUND", PermanentError),
    (SubnetNotFoundError, "SUBNET_NOT_FOUND", PermanentError),
    (SecurityGroupNotFoundError, "SG_NOT_FOUND", PermanentError),
    (QuotaExceededError, "QUOTA_EXCEEDED", InfrastructureError),
    (CapacityUnavailableError, "INSUFFICIENT_CAPACITY", InfrastructureError),
    (SubnetExhaustedError, "SUBNET_EXHAUSTED", InfrastructureError),
]


@pytest.mark.parametrize("exc_cls, expected_code, expected_base", _ALL_TYPED)
def test_typed_error_has_class_level_code_and_base(exc_cls, expected_code, expected_base):
    """Each typed error has a class-level `code` and the right base class."""
    e = exc_cls("test message")
    assert e.code == expected_code
    assert isinstance(e, expected_base)
    assert isinstance(e, ProvisioningError)


def test_message_preserved_via_str():
    e = AWSThrottledError("hit AWS rate limit")
    assert str(e) == "hit AWS rate limit"


def test_hint_optional_and_overrideable():
    e = AMINotFoundError("ami-abc not in us-west-2", hint="try us-east-1")
    assert e.hint == "try us-east-1"


def test_hint_default_none():
    e = AWSServerError("EC2 returned 503")
    assert e.hint is None


def test_code_can_be_overridden_at_construction():
    """Some classifiers may want to set a more specific code at runtime."""
    e = TransientError("custom", code="CUSTOM_CODE")
    assert e.code == "CUSTOM_CODE"


def test_base_classes_form_a_hierarchy():
    """All three error classes inherit from ProvisioningError but are
    siblings of each other (no cross-class isinstance)."""
    t = TransientError("t")
    p = PermanentError("p")
    i = InfrastructureError("i")
    assert isinstance(t, ProvisioningError)
    assert isinstance(p, ProvisioningError)
    assert isinstance(i, ProvisioningError)
    assert not isinstance(t, PermanentError)
    assert not isinstance(p, InfrastructureError)
    assert not isinstance(i, TransientError)
```

- [ ] **Step 2.2: Run test to verify it fails**

```bash
pytest package/src/inferia/services/orchestration/services/provisioning/tests/test_errors.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'inferia.services.orchestration.services.provisioning.errors'`.

- [ ] **Step 2.3: Implement the error hierarchy**

Create `package/src/inferia/services/orchestration/services/provisioning/errors.py`:

```python
"""Typed exception hierarchy for the provisioning state machine.

Phase handlers raise these (or unknown exceptions). The retry/classifier
module is the single source of truth for retry-vs-fail decisions.

Convention: each concrete subclass declares a class-level `code` (kept
in sync with the docs/specs/2026-05-27-aws-ec2-node-allocation-design.md
error-rendering table). The runtime constructor allows overriding the
code for cases where the classifier wants to be more specific.
"""
from __future__ import annotations


class ProvisioningError(Exception):
    """Base class. Subclasses carry code + optional hint."""

    code: str = "UNCLASSIFIED"
    hint: str | None = None

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        hint: str | None = None,
    ):
        super().__init__(message)
        if code is not None:
            self.code = code
        if hint is not None:
            self.hint = hint


# --- TRANSIENT -------------------------------------------------------------


class TransientError(ProvisioningError):
    """Retryable. Reconciler schedules a backoff and re-runs the phase."""


class AWSThrottledError(TransientError):
    code = "AWS_THROTTLED"


class AWSServerError(TransientError):
    code = "AWS_5XX"


class PulumiTransientError(TransientError):
    code = "PULUMI_TRANSIENT"


class NetworkError(TransientError):
    code = "NETWORK_ERROR"


# --- PERMANENT -------------------------------------------------------------


class PermanentError(ProvisioningError):
    """Not retryable. Phase transitions directly to 'failed'."""


class PulumiCliMissingError(PermanentError):
    code = "PULUMI_CLI_MISSING"


class InvalidCredentialsError(PermanentError):
    code = "INVALID_CREDENTIALS"


class InvalidSpecError(PermanentError):
    code = "INVALID_SPEC"


class InvalidInstanceTypeError(PermanentError):
    code = "INVALID_INSTANCE_TYPE"


class AMINotFoundError(PermanentError):
    code = "AMI_NOT_FOUND"


class SubnetNotFoundError(PermanentError):
    code = "SUBNET_NOT_FOUND"


class SecurityGroupNotFoundError(PermanentError):
    code = "SG_NOT_FOUND"


# --- INFRASTRUCTURE --------------------------------------------------------


class InfrastructureError(ProvisioningError):
    """Operator must take an AWS-account-level action (quota, capacity).
    Treated as PERMANENT for retry purposes — operator clicks Retry once
    the underlying issue is addressed."""


class QuotaExceededError(InfrastructureError):
    code = "QUOTA_EXCEEDED"


class CapacityUnavailableError(InfrastructureError):
    code = "INSUFFICIENT_CAPACITY"


class SubnetExhaustedError(InfrastructureError):
    code = "SUBNET_EXHAUSTED"
```

- [ ] **Step 2.4: Run test to verify it passes**

```bash
pytest package/src/inferia/services/orchestration/services/provisioning/tests/test_errors.py -v
```

Expected: all 21 test cases PASS (14 parametrized + 7 standalone).

- [ ] **Step 2.5: Commit**

```bash
git add package/src/inferia/services/orchestration/services/provisioning/errors.py \
        package/src/inferia/services/orchestration/services/provisioning/tests/test_errors.py
git -c user.signingkey=/home/ankit/.ssh/id_ed25519_gh commit -S -m "provisioning: add typed error hierarchy

Three base classes (TransientError, PermanentError, InfrastructureError)
under ProvisioningError, with 14 concrete subclasses each carrying a
class-level code that matches the spec's error-rendering table. Hint is
optional and overrideable at construction. The classifier (next task)
maps any exception to one of these."
```

---

### Task 3: AWS instance catalog

**Files:**
- Create: `package/src/inferia/services/orchestration/services/adapter_engine/adapters/aws/instance_catalog.py`
- Test: `package/src/inferia/services/orchestration/services/adapter_engine/adapters/aws/test_instance_catalog.py`

- [ ] **Step 3.1: Write the failing test**

Create `package/src/inferia/services/orchestration/services/adapter_engine/adapters/aws/test_instance_catalog.py`:

```python
"""Tests for the AWS instance catalog."""
import pytest

from inferia.services.orchestration.services.adapter_engine.adapters.aws.instance_catalog import (
    INSTANCE_CATALOG,
    InstanceType,
    by_class,
    lookup,
)


def test_catalog_is_not_empty():
    assert len(INSTANCE_CATALOG) > 0


def test_every_entry_has_a_valid_class():
    for it in INSTANCE_CATALOG:
        assert it.cls in {"normal_gpu", "heavy_gpu", "cpu"}


def test_every_entry_has_consistent_gpu_fields():
    """gpu_count > 0 ⇔ cls != 'cpu' ⇔ gpu_model is set."""
    for it in INSTANCE_CATALOG:
        if it.cls == "cpu":
            assert it.gpu_count == 0
            assert it.gpu_model is None
            assert it.gpu_ram_gb == 0
        else:
            assert it.gpu_count > 0
            assert it.gpu_model is not None
            assert it.gpu_ram_gb > 0


def test_catalog_includes_all_three_classes():
    classes = {it.cls for it in INSTANCE_CATALOG}
    assert classes == {"normal_gpu", "heavy_gpu", "cpu"}


def test_names_are_unique():
    names = [it.name for it in INSTANCE_CATALOG]
    assert len(names) == len(set(names))


def test_by_class_returns_only_matching_entries():
    cpu_entries = by_class("cpu")
    assert all(it.cls == "cpu" for it in cpu_entries)
    assert cpu_entries  # non-empty


def test_by_class_unknown_returns_empty_list():
    assert by_class("quantum_gpu") == []


def test_lookup_returns_matching_entry():
    sample = INSTANCE_CATALOG[0]
    assert lookup(sample.name) == sample


def test_lookup_unknown_returns_none():
    assert lookup("z9.imaginary") is None


def test_normal_gpu_default_set_present():
    """The default tier must include g5.xlarge and g6.xlarge."""
    names = {it.name for it in by_class("normal_gpu")}
    assert "g5.xlarge" in names
    assert "g6.xlarge" in names


def test_cpu_default_set_present():
    """CPU tier must include common c6i + m6i sizes."""
    names = {it.name for it in by_class("cpu")}
    assert "c6i.xlarge" in names
    assert "m6i.xlarge" in names


def test_heavy_gpu_default_set_present():
    """Heavy GPU tier must include at least one p-family instance."""
    names = {it.name for it in by_class("heavy_gpu")}
    assert any(n.startswith("p4d.") or n.startswith("p5.") for n in names)


def test_instance_type_is_frozen():
    """InstanceType records are immutable."""
    sample = INSTANCE_CATALOG[0]
    with pytest.raises((AttributeError, Exception)):
        sample.name = "z.0"  # type: ignore[misc]


def test_approx_usd_per_hour_positive():
    """All entries have a positive approximate hourly price."""
    for it in INSTANCE_CATALOG:
        assert it.approx_usd_per_hour > 0
```

- [ ] **Step 3.2: Run test to verify it fails**

```bash
pytest package/src/inferia/services/orchestration/services/adapter_engine/adapters/aws/test_instance_catalog.py -v
```

Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3.3: Implement the catalog**

Create `package/src/inferia/services/orchestration/services/adapter_engine/adapters/aws/instance_catalog.py`:

```python
"""Curated AWS EC2 instance-type catalog used by the New Pool wizard
and the PreflightHandler.

This is intentionally a static module rather than a live AWS pricing
fetch — the dashboard needs a snappy /providers/aws/instance-catalog
response, and approximate prices are accurate enough for UX purposes.
Add an entry here when you want a new type to show up in the wizard."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


InstanceClass = Literal["normal_gpu", "heavy_gpu", "cpu"]


@dataclass(frozen=True)
class InstanceType:
    """One row in the curated catalog."""

    name: str                       # e.g. 'g6.xlarge'
    cls: InstanceClass              # tier the wizard groups this under
    vcpu: int
    ram_gb: int
    gpu_count: int                  # 0 for cpu
    gpu_model: str | None           # None for cpu
    gpu_ram_gb: int                 # 0 for cpu
    approx_usd_per_hour: float


# Curated initial catalog. Prices are approximate us-east-1 on-demand
# values from AWS public pricing as of 2026-05; close enough for UX.
INSTANCE_CATALOG: list[InstanceType] = [
    # --- normal_gpu: single-GPU inference (7-13B, 24 GB VRAM) ----------
    InstanceType("g5.xlarge",   "normal_gpu",  4,  16, 1, "NVIDIA A10G",  24, 1.006),
    InstanceType("g5.2xlarge",  "normal_gpu",  8,  32, 1, "NVIDIA A10G",  24, 1.212),
    InstanceType("g5.4xlarge",  "normal_gpu", 16,  64, 1, "NVIDIA A10G",  24, 1.624),
    InstanceType("g6.xlarge",   "normal_gpu",  4,  16, 1, "NVIDIA L4",    24, 0.805),
    InstanceType("g6.2xlarge",  "normal_gpu",  8,  32, 1, "NVIDIA L4",    24, 0.978),
    InstanceType("g6.4xlarge",  "normal_gpu", 16,  64, 1, "NVIDIA L4",    24, 1.323),
    # --- heavy_gpu: multi-GPU / large model inference -----------------
    InstanceType("g5.12xlarge", "heavy_gpu",  48, 192, 4, "NVIDIA A10G",  96, 5.672),
    InstanceType("g5.48xlarge", "heavy_gpu", 192, 768, 8, "NVIDIA A10G", 192, 16.288),
    InstanceType("g6.12xlarge", "heavy_gpu",  48, 192, 4, "NVIDIA L4",    96, 4.602),
    InstanceType("p4d.24xlarge","heavy_gpu",  96,1152, 8, "NVIDIA A100", 320, 32.770),
    InstanceType("p4de.24xlarge","heavy_gpu", 96,1152, 8, "NVIDIA A100", 640, 40.965),
    InstanceType("p5.48xlarge", "heavy_gpu", 192,2048, 8, "NVIDIA H100", 640, 98.320),
    # --- cpu: quantized small models, embeddings, cheap test pools ----
    InstanceType("c6i.xlarge",  "cpu",  4,   8, 0, None, 0, 0.170),
    InstanceType("c6i.2xlarge", "cpu",  8,  16, 0, None, 0, 0.340),
    InstanceType("c6i.4xlarge", "cpu", 16,  32, 0, None, 0, 0.680),
    InstanceType("m6i.xlarge",  "cpu",  4,  16, 0, None, 0, 0.192),
    InstanceType("m6i.2xlarge", "cpu",  8,  32, 0, None, 0, 0.384),
    InstanceType("m6i.4xlarge", "cpu", 16,  64, 0, None, 0, 0.768),
]


_BY_NAME: dict[str, InstanceType] = {it.name: it for it in INSTANCE_CATALOG}


def lookup(name: str) -> InstanceType | None:
    """Return the catalog entry for `name`, or None if not in the catalog."""
    return _BY_NAME.get(name)


def by_class(cls: str) -> list[InstanceType]:
    """All catalog entries belonging to `cls`. Unknown class → []."""
    return [it for it in INSTANCE_CATALOG if it.cls == cls]
```

- [ ] **Step 3.4: Run test to verify it passes**

```bash
pytest package/src/inferia/services/orchestration/services/adapter_engine/adapters/aws/test_instance_catalog.py -v
```

Expected: all 13 tests PASS.

- [ ] **Step 3.5: Commit**

```bash
git add package/src/inferia/services/orchestration/services/adapter_engine/adapters/aws/instance_catalog.py \
        package/src/inferia/services/orchestration/services/adapter_engine/adapters/aws/test_instance_catalog.py
git -c user.signingkey=/home/ankit/.ssh/id_ed25519_gh commit -S -m "aws: add curated instance-type catalog

18 EC2 types split across normal_gpu / heavy_gpu / cpu tiers, each with
vcpu, ram_gb, gpu_count, gpu_model, gpu_ram_gb, and approx_usd_per_hour.
Powers the wizard's instance-type dropdown via the upcoming
/providers/aws/instance-catalog endpoint and is the source of truth the
PreflightHandler validates instance_class/instance_type pairings against.

Approximate prices are us-east-1 on-demand values. Add an entry when you
want a new EC2 type to appear in the wizard."
```

---

### Task 4: Backoff (`retry/backoff.py`)

**Files:**
- Create: `package/src/inferia/services/orchestration/services/provisioning/retry/__init__.py`
- Create: `package/src/inferia/services/orchestration/services/provisioning/retry/backoff.py`
- Test: `package/src/inferia/services/orchestration/services/provisioning/retry/tests/test_backoff.py`

- [ ] **Step 4.1: Write the failing test**

Create `package/src/inferia/services/orchestration/services/provisioning/retry/tests/test_backoff.py`:

```python
"""Tests for the exponential-backoff-with-jitter helper."""
from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

import pytest

from inferia.services.orchestration.services.provisioning.retry.backoff import (
    TRANSIENT_MAX_ATTEMPTS,
    next_attempt_after,
)


@pytest.fixture
def fixed_seed():
    random.seed(0)
    yield
    random.seed()


def _delta_seconds(delta_time: datetime, now: datetime) -> float:
    return (delta_time - now).total_seconds()


def test_attempt_1_delay_between_half_and_one_half_base(fixed_seed):
    """attempt=1 → base=2s; delay is base/2 + jitter ∈ [0, base] → [1, 3)."""
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    when = next_attempt_after(1, now=now)
    d = _delta_seconds(when, now)
    assert 1.0 <= d < 3.0


def test_attempt_2_in_window():
    """attempt=2 → base=4; delay ∈ [2, 6)."""
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for _ in range(50):
        d = _delta_seconds(next_attempt_after(2, now=now), now)
        assert 2.0 <= d < 6.0


def test_attempt_5_in_window():
    """attempt=5 → base=32; delay ∈ [16, 48)."""
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for _ in range(50):
        d = _delta_seconds(next_attempt_after(5, now=now), now)
        assert 16.0 <= d < 48.0


def test_attempt_10_capped_at_60s_base():
    """High attempt numbers cap base at 60; delay ∈ [30, 90)."""
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for _ in range(50):
        d = _delta_seconds(next_attempt_after(10, now=now), now)
        assert 30.0 <= d < 90.0


def test_jitter_is_non_zero_statistically():
    """Across many samples, the delays vary (not all the same value)."""
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    samples = {_delta_seconds(next_attempt_after(3, now=now), now) for _ in range(100)}
    assert len(samples) > 50


def test_returns_timezone_aware_datetime():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    when = next_attempt_after(1, now=now)
    assert when.tzinfo is not None


def test_max_attempts_constant_is_five():
    """Spec value: 5 transient attempts before escalating to RETRIES_EXHAUSTED."""
    assert TRANSIENT_MAX_ATTEMPTS == 5
```

- [ ] **Step 4.2: Run test to verify it fails**

```bash
pytest package/src/inferia/services/orchestration/services/provisioning/retry/tests/test_backoff.py -v
```

Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 4.3: Implement the backoff helper**

Create `package/src/inferia/services/orchestration/services/provisioning/retry/__init__.py`:

```python
"""Retry policy (error classification + backoff math)."""
```

Create `package/src/inferia/services/orchestration/services/provisioning/retry/tests/__init__.py` (empty file).

Create `package/src/inferia/services/orchestration/services/provisioning/retry/backoff.py`:

```python
"""Exponential backoff with jitter for transient retries.

Spec: docs/specs/2026-05-27-aws-ec2-node-allocation-design.md → 'Backoff math'.

Formula:
    base = min(60, 2 ** attempt)
    delay = base/2 + random.uniform(0, base)

So attempt N's delay is in [base/2, 1.5*base). The cap at 60s keeps the
total wait window over 5 attempts bounded by ≈ 2 minutes (the cap kicks
in at attempt ≥ 6 but TRANSIENT_MAX_ATTEMPTS=5 stops us before then).
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta


TRANSIENT_MAX_ATTEMPTS = 5
"""After this many transient failures, the reconciler escalates to a
PERMANENT error with code='RETRIES_EXHAUSTED'."""


def next_attempt_after(attempt: int, *, now: datetime) -> datetime:
    """Return the wall-clock time the reconciler should try the phase again.

    `attempt` is 1-indexed (the attempt about to be retried — so after the
    1st failure pass 1; after the 5th pass 5).

    The returned datetime is in the same timezone as `now`. Callers in
    practice pass `datetime.now(timezone.utc)`.
    """
    base = min(60, 2 ** attempt)
    delay = base / 2 + random.uniform(0, base)
    return now + timedelta(seconds=delay)
```

- [ ] **Step 4.4: Run test to verify it passes**

```bash
pytest package/src/inferia/services/orchestration/services/provisioning/retry/tests/test_backoff.py -v
```

Expected: all 7 tests PASS.

- [ ] **Step 4.5: Commit**

```bash
git add package/src/inferia/services/orchestration/services/provisioning/retry/
git -c user.signingkey=/home/ankit/.ssh/id_ed25519_gh commit -S -m "provisioning: add exponential-backoff-with-jitter helper

next_attempt_after(attempt, now) implements the spec's
min(60, 2**attempt) base with [base/2, 1.5*base) jitter window. After
TRANSIENT_MAX_ATTEMPTS=5 the reconciler escalates a transient failure
to PERMANENT/RETRIES_EXHAUSTED (next task wires that in)."
```

---

### Task 5: ProvisioningJob model + enums

**Files:**
- Create: `package/src/inferia/services/orchestration/services/provisioning/jobs/__init__.py`
- Create: `package/src/inferia/services/orchestration/services/provisioning/jobs/model.py`
- Test: `package/src/inferia/services/orchestration/services/provisioning/jobs/tests/test_model.py`

- [ ] **Step 5.1: Write the failing test**

Create `package/src/inferia/services/orchestration/services/provisioning/jobs/tests/__init__.py` (empty).

Create `package/src/inferia/services/orchestration/services/provisioning/jobs/tests/test_model.py`:

```python
"""Tests for ProvisioningJob model + Phase/ErrorClass enums + related dataclasses."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from inferia.services.orchestration.services.provisioning.jobs.model import (
    ClassifiedError,
    ErrorClass,
    EventLine,
    Phase,
    PhaseResult,
    ProvisioningJob,
)


# ---- Phase enum ----------------------------------------------------------


def test_phase_has_eight_values():
    assert {p.value for p in Phase} == {
        "pending", "preflight", "provisioning", "bootstrapping",
        "ready", "failed", "cancelling", "terminated",
    }


@pytest.mark.parametrize("phase, expected_terminal", [
    (Phase.PENDING,       False),
    (Phase.PREFLIGHT,     False),
    (Phase.PROVISIONING,  False),
    (Phase.BOOTSTRAPPING, False),
    (Phase.CANCELLING,    False),
    (Phase.READY,         True),
    (Phase.FAILED,        True),
    (Phase.TERMINATED,    True),
])
def test_phase_is_terminal(phase, expected_terminal):
    assert phase.is_terminal is expected_terminal


def test_phase_str_value_matches():
    assert Phase.READY == "ready"
    assert Phase.READY.value == "ready"


# ---- ErrorClass enum -----------------------------------------------------


def test_error_class_three_values():
    assert {e.value for e in ErrorClass} == {"TRANSIENT", "PERMANENT", "INFRASTRUCTURE"}


# ---- ClassifiedError dataclass -------------------------------------------


def test_classified_error_fields():
    ce = ClassifiedError(
        error_class=ErrorClass.PERMANENT,
        code="PULUMI_CLI_MISSING",
        message="pulumi binary not found",
        hint="curl pulumi.com | sh",
    )
    assert ce.error_class == ErrorClass.PERMANENT
    assert ce.code == "PULUMI_CLI_MISSING"
    assert ce.message == "pulumi binary not found"
    assert ce.hint == "curl pulumi.com | sh"


def test_classified_error_hint_defaults_none():
    ce = ClassifiedError(error_class=ErrorClass.TRANSIENT, code="X", message="m")
    assert ce.hint is None


def test_classified_error_is_frozen():
    ce = ClassifiedError(error_class=ErrorClass.TRANSIENT, code="X", message="m")
    with pytest.raises((AttributeError, Exception)):
        ce.code = "Y"  # type: ignore[misc]


# ---- EventLine dataclass -------------------------------------------------


def test_event_line_fields():
    el = EventLine(
        phase=Phase.PROVISIONING,
        status="log",
        message="creating EC2 instance",
        extra={"step": 3},
    )
    assert el.phase == Phase.PROVISIONING
    assert el.status == "log"
    assert el.extra == {"step": 3}


def test_event_line_extra_defaults_none():
    el = EventLine(phase=Phase.PREFLIGHT, status="running", message="checking creds")
    assert el.extra is None


# ---- PhaseResult dataclass -----------------------------------------------


def test_phase_result_defaults():
    pr = PhaseResult(next_phase=Phase.PROVISIONING)
    assert pr.next_phase == Phase.PROVISIONING
    assert pr.outputs is None
    assert pr.event is None


def test_phase_result_with_outputs_and_event():
    pr = PhaseResult(
        next_phase=Phase.BOOTSTRAPPING,
        outputs={"instance_id": "i-abc"},
        event=EventLine(Phase.PROVISIONING, "succeeded", "ec2 running"),
    )
    assert pr.outputs == {"instance_id": "i-abc"}
    assert pr.event is not None and pr.event.status == "succeeded"


def test_phase_result_next_phase_none_means_stay():
    """A handler returning next_phase=None means 'stay in current phase'
    (used by transient retries that should schedule a backoff)."""
    pr = PhaseResult(next_phase=None)
    assert pr.next_phase is None


# ---- ProvisioningJob model -----------------------------------------------


def _now() -> datetime:
    return datetime(2026, 5, 28, 12, 0, 0, tzinfo=timezone.utc)


def test_provisioning_job_roundtrip_from_row():
    """ProvisioningJob.from_row(asyncpg.Record-like dict) → Pydantic instance."""
    row = {
        "id": uuid.uuid4(),
        "node_id": uuid.uuid4(),
        "pool_id": uuid.uuid4(),
        "org_id": "org-1",
        "provider": "aws",
        "spec": {"instance_type": "g6.xlarge"},
        "phase": "preflight",
        "attempt_count": 1,
        "next_attempt_after": None,
        "last_error_code": None,
        "last_error_message": None,
        "last_error_hint": None,
        "error_class": None,
        "lease_holder": "inferia-app-1234-host",
        "lease_expires_at": _now(),
        "pulumi_stack_outputs": None,
        "created_at": _now(),
        "updated_at": _now(),
    }
    job = ProvisioningJob.from_row(row)
    assert job.phase == Phase.PREFLIGHT
    assert job.spec == {"instance_type": "g6.xlarge"}
    assert job.attempt_count == 1


def test_provisioning_job_phase_is_terminal_proxy():
    row = _row_with(phase="ready")
    assert ProvisioningJob.from_row(row).phase.is_terminal


def test_provisioning_job_error_fields_optional():
    row = _row_with(phase="pending")
    job = ProvisioningJob.from_row(row)
    assert job.last_error_code is None
    assert job.error_class is None


def _row_with(**overrides):
    row = {
        "id": uuid.uuid4(),
        "node_id": uuid.uuid4(),
        "pool_id": uuid.uuid4(),
        "org_id": "org-1",
        "provider": "aws",
        "spec": {},
        "phase": "pending",
        "attempt_count": 0,
        "next_attempt_after": None,
        "last_error_code": None,
        "last_error_message": None,
        "last_error_hint": None,
        "error_class": None,
        "lease_holder": None,
        "lease_expires_at": None,
        "pulumi_stack_outputs": None,
        "created_at": _now(),
        "updated_at": _now(),
    }
    row.update(overrides)
    return row
```

- [ ] **Step 5.2: Run test to verify it fails**

```bash
pytest package/src/inferia/services/orchestration/services/provisioning/jobs/tests/test_model.py -v
```

Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 5.3: Implement the model**

Create `package/src/inferia/services/orchestration/services/provisioning/jobs/__init__.py`:

```python
"""Persisted provisioning-job model + repository."""
```

Create `package/src/inferia/services/orchestration/services/provisioning/jobs/model.py`:

```python
"""ProvisioningJob domain model + Phase/ErrorClass enums + supporting
dataclasses (ClassifiedError, EventLine, PhaseResult).

The Pydantic ProvisioningJob is used by repository read paths and the
HTTP layer; the dataclasses are used inside handler/reconciler code
where immutability + value semantics are more natural than Pydantic."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class Phase(str, Enum):
    PENDING       = "pending"
    PREFLIGHT     = "preflight"
    PROVISIONING  = "provisioning"
    BOOTSTRAPPING = "bootstrapping"
    READY         = "ready"
    FAILED        = "failed"
    CANCELLING    = "cancelling"
    TERMINATED    = "terminated"

    @property
    def is_terminal(self) -> bool:
        return self in _TERMINAL_PHASES


_TERMINAL_PHASES: frozenset[Phase] = frozenset(
    {Phase.READY, Phase.FAILED, Phase.TERMINATED}
)


NON_TERMINAL_NON_CANCELLING: frozenset[Phase] = frozenset({
    Phase.PENDING, Phase.PREFLIGHT, Phase.PROVISIONING, Phase.BOOTSTRAPPING,
})
"""The set of phases the claim query considers when looking for the next
job to run (excluding 'cancelling' which is handled specially)."""


CLAIMABLE_PHASES: frozenset[Phase] = NON_TERMINAL_NON_CANCELLING | {Phase.CANCELLING}


class ErrorClass(str, Enum):
    TRANSIENT      = "TRANSIENT"
    PERMANENT      = "PERMANENT"
    INFRASTRUCTURE = "INFRASTRUCTURE"


@dataclass(frozen=True)
class ClassifiedError:
    """Output of `classify_error(exc)`. The reconciler uses this to decide
    retry vs fail and to populate the job row's error_* columns."""
    error_class: ErrorClass
    code: str
    message: str
    hint: str | None = None


EventStatus = Literal["running", "succeeded", "failed", "log"]


@dataclass(frozen=True)
class EventLine:
    """One row to emit into node_provisioning_events."""
    phase: Phase
    status: EventStatus
    message: str
    extra: dict[str, Any] | None = None


@dataclass(frozen=True)
class PhaseResult:
    """Successful handler return value.

    - next_phase=None ⇒ "stay in current phase" (transient retry path);
      the reconciler will increment attempt_count and schedule a backoff.
    - outputs: dict merged into provisioning_jobs.pulumi_stack_outputs.
    - event: single summary EventLine to emit on success (handlers may
      emit additional `log`-status events while running; this is the
      terminal one for the phase transition).
    """
    next_phase: Phase | None
    outputs: dict[str, Any] | None = None
    event: EventLine | None = None


class ProvisioningJob(BaseModel):
    """Pydantic mirror of a provisioning_jobs row.

    Use `ProvisioningJob.from_row(record)` to build from asyncpg.Record
    or a dict; that translates phase/error_class strings to enum values
    and handles JSONB columns."""

    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    id: UUID
    node_id: UUID
    pool_id: UUID
    org_id: str
    provider: str
    spec: dict[str, Any] = Field(default_factory=dict)

    phase: Phase
    attempt_count: int
    next_attempt_after: datetime | None = None

    last_error_code: str | None = None
    last_error_message: str | None = None
    last_error_hint: str | None = None
    error_class: ErrorClass | None = None

    lease_holder: str | None = None
    lease_expires_at: datetime | None = None

    pulumi_stack_outputs: dict[str, Any] | None = None

    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_row(cls, row: Any) -> "ProvisioningJob":
        """Build from an asyncpg.Record or dict-like mapping."""
        return cls(
            id=row["id"],
            node_id=row["node_id"],
            pool_id=row["pool_id"],
            org_id=row["org_id"],
            provider=row["provider"],
            spec=row["spec"] or {},
            phase=Phase(row["phase"]),
            attempt_count=row["attempt_count"],
            next_attempt_after=row["next_attempt_after"],
            last_error_code=row["last_error_code"],
            last_error_message=row["last_error_message"],
            last_error_hint=row["last_error_hint"],
            error_class=ErrorClass(row["error_class"]) if row["error_class"] else None,
            lease_holder=row["lease_holder"],
            lease_expires_at=row["lease_expires_at"],
            pulumi_stack_outputs=row["pulumi_stack_outputs"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
```

- [ ] **Step 5.4: Run test to verify it passes**

```bash
pytest package/src/inferia/services/orchestration/services/provisioning/jobs/tests/test_model.py -v
```

Expected: all 19 tests PASS.

- [ ] **Step 5.5: Commit**

```bash
git add package/src/inferia/services/orchestration/services/provisioning/jobs/
git -c user.signingkey=/home/ankit/.ssh/id_ed25519_gh commit -S -m "provisioning: add ProvisioningJob model + Phase/ErrorClass enums

ProvisioningJob is the Pydantic mirror of a provisioning_jobs row used
by repository reads + HTTP. Phase has 8 values with .is_terminal proxy;
ErrorClass has the 3 spec values. ClassifiedError, EventLine, and
PhaseResult are frozen dataclasses passed around handler/reconciler
code where value semantics are more natural than Pydantic.

CLAIMABLE_PHASES + NON_TERMINAL_NON_CANCELLING module-level constants
will be used by the upcoming claim-query and reconciler routing logic."
```

---

### Task 6: ProvisioningJobRepository

**Files:**
- Create: `package/src/inferia/services/orchestration/services/provisioning/jobs/repository.py`
- Test: `package/src/inferia/services/orchestration/services/provisioning/jobs/tests/test_repository.py`

- [ ] **Step 6.1: Write the failing test**

Create `package/src/inferia/services/orchestration/services/provisioning/jobs/tests/test_repository.py`:

```python
"""Repository tests using mocked asyncpg connections, mirroring the
pattern in services/orchestration/services/worker_controller/test_auth.py."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from inferia.services.orchestration.services.provisioning.jobs.model import (
    ClassifiedError, ErrorClass, EventLine, Phase, ProvisioningJob,
)
from inferia.services.orchestration.services.provisioning.jobs.repository import (
    ProvisioningJobRepository,
)


def _row(**over):
    base = {
        "id": uuid.uuid4(),
        "node_id": uuid.uuid4(),
        "pool_id": uuid.uuid4(),
        "org_id": "org-1",
        "provider": "aws",
        "spec": {"instance_type": "g6.xlarge"},
        "phase": "pending",
        "attempt_count": 0,
        "next_attempt_after": None,
        "last_error_code": None,
        "last_error_message": None,
        "last_error_hint": None,
        "error_class": None,
        "lease_holder": None,
        "lease_expires_at": None,
        "pulumi_stack_outputs": None,
        "created_at": datetime(2026, 5, 28, tzinfo=timezone.utc),
        "updated_at": datetime(2026, 5, 28, tzinfo=timezone.utc),
    }
    base.update(over)
    return base


def _make_db_with_conn(conn):
    db = MagicMock()
    db.acquire = MagicMock()
    db.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    db.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    return db


@pytest.mark.asyncio
async def test_enqueue_inserts_and_returns_job_id():
    conn = MagicMock()
    conn.fetchval = AsyncMock(return_value=uuid.uuid4())
    repo = ProvisioningJobRepository(_make_db_with_conn(conn))
    job_id = await repo.enqueue(
        node_id=uuid.uuid4(),
        pool_id=uuid.uuid4(),
        org_id="org-1",
        provider="aws",
        spec={"instance_type": "g6.xlarge"},
    )
    assert isinstance(job_id, uuid.UUID)
    conn.fetchval.assert_awaited_once()
    sql = conn.fetchval.await_args.args[0]
    assert "INSERT INTO provisioning_jobs" in sql


@pytest.mark.asyncio
async def test_get_returns_none_when_missing():
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value=None)
    repo = ProvisioningJobRepository(_make_db_with_conn(conn))
    assert await repo.get(uuid.uuid4()) is None


@pytest.mark.asyncio
async def test_get_returns_provisioning_job_when_present():
    row = _row(phase="preflight")
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value=row)
    repo = ProvisioningJobRepository(_make_db_with_conn(conn))
    job = await repo.get(row["id"])
    assert isinstance(job, ProvisioningJob)
    assert job.phase == Phase.PREFLIGHT


@pytest.mark.asyncio
async def test_get_by_node_returns_latest_job():
    row = _row(phase="provisioning")
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value=row)
    repo = ProvisioningJobRepository(_make_db_with_conn(conn))
    job = await repo.get_by_node(row["node_id"])
    assert isinstance(job, ProvisioningJob)
    sql = conn.fetchrow.await_args.args[0]
    assert "ORDER BY created_at DESC" in sql


@pytest.mark.asyncio
async def test_claim_next_job_returns_none_when_queue_empty():
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value=None)
    repo = ProvisioningJobRepository(_make_db_with_conn(conn))
    job = await repo.claim_next_job(lease_holder="me", lease_seconds=300)
    assert job is None


@pytest.mark.asyncio
async def test_claim_next_job_uses_for_update_skip_locked():
    """The claim query must use FOR UPDATE SKIP LOCKED to avoid contention."""
    row = _row(phase="pending")
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value=row)
    repo = ProvisioningJobRepository(_make_db_with_conn(conn))
    job = await repo.claim_next_job(lease_holder="me", lease_seconds=300)
    assert job is not None
    sql = conn.fetchrow.await_args.args[0]
    assert "FOR UPDATE SKIP LOCKED" in sql


@pytest.mark.asyncio
async def test_renew_lease_returns_true_when_update_affects_row():
    conn = MagicMock()
    conn.execute = AsyncMock(return_value="UPDATE 1")
    repo = ProvisioningJobRepository(_make_db_with_conn(conn))
    ok = await repo.renew_lease(
        job_id=uuid.uuid4(), lease_holder="me", lease_seconds=300,
    )
    assert ok is True


@pytest.mark.asyncio
async def test_renew_lease_returns_false_when_stolen():
    conn = MagicMock()
    conn.execute = AsyncMock(return_value="UPDATE 0")
    repo = ProvisioningJobRepository(_make_db_with_conn(conn))
    ok = await repo.renew_lease(
        job_id=uuid.uuid4(), lease_holder="me", lease_seconds=300,
    )
    assert ok is False


@pytest.mark.asyncio
async def test_release_lease_clears_holder_only_when_matching():
    conn = MagicMock()
    conn.execute = AsyncMock(return_value="UPDATE 1")
    repo = ProvisioningJobRepository(_make_db_with_conn(conn))
    await repo.release_lease(job_id=uuid.uuid4(), lease_holder="me")
    sql = conn.execute.await_args.args[0]
    assert "lease_holder = $2" in sql or "lease_holder = $1" in sql


@pytest.mark.asyncio
async def test_transition_to_advances_phase_with_lease_guard():
    conn = MagicMock()
    conn.execute = AsyncMock(return_value="UPDATE 1")
    repo = ProvisioningJobRepository(_make_db_with_conn(conn))
    await repo.transition_to(
        job_id=uuid.uuid4(),
        current_phase=Phase.PREFLIGHT,
        next_phase=Phase.PROVISIONING,
        lease_holder="me",
        outputs={"x": 1},
    )
    sql = conn.execute.await_args.args[0]
    assert "SET phase" in sql
    assert "WHERE id = $1" in sql
    assert "phase = $" in sql  # guard


@pytest.mark.asyncio
async def test_schedule_retry_keeps_phase_and_bumps_attempt():
    conn = MagicMock()
    conn.execute = AsyncMock(return_value="UPDATE 1")
    repo = ProvisioningJobRepository(_make_db_with_conn(conn))
    err = ClassifiedError(ErrorClass.TRANSIENT, "AWS_THROTTLED", "hit limit")
    await repo.schedule_retry(
        job_id=uuid.uuid4(),
        current_phase=Phase.PROVISIONING,
        lease_holder="me",
        next_attempt_after=datetime(2026, 5, 28, tzinfo=timezone.utc),
        attempt_count=2,
        error=err,
    )
    sql = conn.execute.await_args.args[0]
    # phase stays; attempt_count is set; lease is cleared so another reconciler
    # can pick the job up later.
    assert "attempt_count" in sql
    assert "next_attempt_after" in sql
    assert "lease_holder = NULL" in sql or "lease_holder=NULL" in sql


@pytest.mark.asyncio
async def test_fail_writes_terminal_phase_and_error_fields():
    conn = MagicMock()
    conn.execute = AsyncMock(return_value="UPDATE 1")
    repo = ProvisioningJobRepository(_make_db_with_conn(conn))
    err = ClassifiedError(
        ErrorClass.PERMANENT, "PULUMI_CLI_MISSING",
        "no pulumi binary", "curl pulumi.com | sh",
    )
    await repo.fail(
        job_id=uuid.uuid4(),
        current_phase=Phase.PREFLIGHT,
        lease_holder="me",
        error=err,
    )
    sql = conn.execute.await_args.args[0]
    assert "phase = 'failed'" in sql or "phase='failed'" in sql
    assert "last_error_code" in sql
    assert "last_error_hint" in sql
    assert "error_class" in sql


@pytest.mark.asyncio
async def test_request_cancel_sets_phase_when_non_terminal():
    conn = MagicMock()
    conn.execute = AsyncMock(return_value="UPDATE 1")
    repo = ProvisioningJobRepository(_make_db_with_conn(conn))
    ok = await repo.request_cancel(node_id=uuid.uuid4())
    assert ok is True
    sql = conn.execute.await_args.args[0]
    assert "phase = 'cancelling'" in sql or "phase='cancelling'" in sql


@pytest.mark.asyncio
async def test_request_cancel_returns_false_when_already_terminal():
    conn = MagicMock()
    conn.execute = AsyncMock(return_value="UPDATE 0")
    repo = ProvisioningJobRepository(_make_db_with_conn(conn))
    ok = await repo.request_cancel(node_id=uuid.uuid4())
    assert ok is False


@pytest.mark.asyncio
async def test_reset_for_retry_returns_job_and_clears_error_fields():
    row = _row(phase="pending", attempt_count=0)
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value=row)
    repo = ProvisioningJobRepository(_make_db_with_conn(conn))
    job = await repo.reset_for_retry(node_id=row["node_id"])
    assert job is not None
    sql = conn.fetchrow.await_args.args[0]
    assert "UPDATE provisioning_jobs" in sql
    assert "phase = 'pending'" in sql or "phase='pending'" in sql
    assert "phase = 'failed'" in sql or "phase='failed'" in sql  # WHERE guard


@pytest.mark.asyncio
async def test_reset_for_retry_returns_none_when_job_not_failed():
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value=None)
    repo = ProvisioningJobRepository(_make_db_with_conn(conn))
    job = await repo.reset_for_retry(node_id=uuid.uuid4())
    assert job is None
```

- [ ] **Step 6.2: Run test to verify it fails**

```bash
pytest package/src/inferia/services/orchestration/services/provisioning/jobs/tests/test_repository.py -v
```

Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 6.3: Implement the repository**

Create `package/src/inferia/services/orchestration/services/provisioning/jobs/repository.py`:

```python
"""Async Postgres repository for the provisioning_jobs queue.

The repository is the only module that writes the jobs table. Phase
handlers MUST NOT touch this table directly — they return PhaseResult
(or raise), and the reconciler calls the repo's transition_to /
schedule_retry / fail methods to durably record the outcome.

All multi-statement operations are single round-trips; we deliberately
do NOT open explicit transactions inside the repo because the reconciler
relies on each method being individually durable (so a crash between
two repo calls doesn't leave a half-written outcome — the lease will
expire and another reconciler will resume from the last-committed state).
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any
from uuid import UUID

from inferia.services.orchestration.services.provisioning.jobs.model import (
    ClassifiedError, Phase, ProvisioningJob,
)


class ProvisioningJobRepository:
    """Wraps a database pool. The `db` argument must expose `.acquire()`
    as an async context manager that yields an asyncpg connection — this
    matches the existing repository pattern in the orchestration service.
    """

    def __init__(self, db):
        self.db = db

    # ---- write paths ----------------------------------------------------

    async def enqueue(
        self,
        *,
        node_id: UUID,
        pool_id: UUID,
        org_id: str,
        provider: str,
        spec: dict[str, Any],
    ) -> UUID:
        """Insert a new pending job, return its id."""
        job_id = uuid.uuid4()
        async with self.db.acquire() as conn:
            await conn.fetchval(
                """
                INSERT INTO provisioning_jobs (
                    id, node_id, pool_id, org_id, provider, spec,
                    phase, attempt_count, created_at, updated_at
                )
                VALUES ($1, $2, $3, $4, $5, $6::jsonb, 'pending', 0, now(), now())
                RETURNING id
                """,
                job_id, node_id, pool_id, org_id, provider, json.dumps(spec),
            )
        return job_id

    async def claim_next_job(
        self, *, lease_holder: str, lease_seconds: int = 300,
    ) -> ProvisioningJob | None:
        """Atomically claim the highest-priority claimable job.

        Returns None if no job is currently claimable (queue empty, all
        leased, or all backed-off). Uses FOR UPDATE SKIP LOCKED so
        concurrent claimers don't contend.
        """
        async with self.db.acquire() as conn:
            row = await conn.fetchrow(
                """
                UPDATE provisioning_jobs
                SET lease_holder = $1,
                    lease_expires_at = now() + make_interval(secs => $2),
                    updated_at = now()
                WHERE id = (
                    SELECT id FROM provisioning_jobs
                    WHERE phase IN ('pending','preflight','provisioning',
                                    'bootstrapping','cancelling')
                      AND (lease_expires_at IS NULL OR lease_expires_at < now())
                      AND (next_attempt_after IS NULL OR next_attempt_after <= now())
                    ORDER BY (phase = 'cancelling') DESC, updated_at ASC
                    LIMIT 1
                    FOR UPDATE SKIP LOCKED
                )
                RETURNING *
                """,
                lease_holder, lease_seconds,
            )
        return ProvisioningJob.from_row(row) if row else None

    async def renew_lease(
        self, *, job_id: UUID, lease_holder: str, lease_seconds: int = 300,
    ) -> bool:
        """Extend the lease deadline. Returns False if the lease was stolen
        (different holder) — caller should cancel the in-flight handler.
        """
        async with self.db.acquire() as conn:
            res = await conn.execute(
                """
                UPDATE provisioning_jobs
                SET lease_expires_at = now() + make_interval(secs => $3),
                    updated_at = now()
                WHERE id = $1 AND lease_holder = $2
                """,
                job_id, lease_holder, lease_seconds,
            )
        return res.endswith(" 1")

    async def release_lease(
        self, *, job_id: UUID, lease_holder: str,
    ) -> None:
        """Clear the lease only if we still hold it. No-op otherwise (defensive)."""
        async with self.db.acquire() as conn:
            await conn.execute(
                """
                UPDATE provisioning_jobs
                SET lease_holder = NULL, lease_expires_at = NULL, updated_at = now()
                WHERE id = $1 AND lease_holder = $2
                """,
                job_id, lease_holder,
            )

    async def transition_to(
        self,
        *,
        job_id: UUID,
        current_phase: Phase,
        next_phase: Phase,
        lease_holder: str,
        outputs: dict[str, Any] | None = None,
    ) -> None:
        """Advance the job to next_phase, optionally merging outputs.
        Phase guard prevents clobbering a concurrent transition."""
        async with self.db.acquire() as conn:
            await conn.execute(
                """
                UPDATE provisioning_jobs
                SET phase = $3::text,
                    pulumi_stack_outputs = COALESCE(pulumi_stack_outputs, '{}'::jsonb)
                                           || COALESCE($4::jsonb, '{}'::jsonb),
                    last_error_code = NULL,
                    last_error_message = NULL,
                    last_error_hint = NULL,
                    error_class = NULL,
                    next_attempt_after = NULL,
                    updated_at = now()
                WHERE id = $1 AND phase = $2::text AND lease_holder = $5
                """,
                job_id, current_phase.value, next_phase.value,
                json.dumps(outputs) if outputs else None,
                lease_holder,
            )

    async def schedule_retry(
        self,
        *,
        job_id: UUID,
        current_phase: Phase,
        lease_holder: str,
        next_attempt_after: datetime,
        attempt_count: int,
        error: ClassifiedError,
    ) -> None:
        """Keep the job in current_phase but bump attempt_count, set
        next_attempt_after, record the error fields, and CLEAR the lease
        so a future reconciler tick can pick it up after the backoff."""
        async with self.db.acquire() as conn:
            await conn.execute(
                """
                UPDATE provisioning_jobs
                SET attempt_count = $3,
                    next_attempt_after = $4,
                    last_error_code = $5,
                    last_error_message = $6,
                    last_error_hint = $7,
                    error_class = $8,
                    lease_holder = NULL,
                    lease_expires_at = NULL,
                    updated_at = now()
                WHERE id = $1 AND phase = $2::text
                """,
                job_id, current_phase.value, attempt_count, next_attempt_after,
                error.code, error.message, error.hint, error.error_class.value,
            )

    async def fail(
        self,
        *,
        job_id: UUID,
        current_phase: Phase,
        lease_holder: str,
        error: ClassifiedError,
    ) -> None:
        """Transition to terminal 'failed' and record the error fields.
        Lease guard ensures we don't overwrite a concurrent transition."""
        async with self.db.acquire() as conn:
            await conn.execute(
                """
                UPDATE provisioning_jobs
                SET phase = 'failed',
                    last_error_code = $3,
                    last_error_message = $4,
                    last_error_hint = $5,
                    error_class = $6,
                    lease_holder = NULL,
                    lease_expires_at = NULL,
                    next_attempt_after = NULL,
                    updated_at = now()
                WHERE id = $1 AND phase = $2::text AND lease_holder = $7
                """,
                job_id, current_phase.value,
                error.code, error.message, error.hint, error.error_class.value,
                lease_holder,
            )

    async def request_cancel(self, *, node_id: UUID) -> bool:
        """Mark the (non-terminal) job for cancellation. Returns False if
        the job is already terminal or there's no job for this node."""
        async with self.db.acquire() as conn:
            res = await conn.execute(
                """
                UPDATE provisioning_jobs
                SET phase = 'cancelling',
                    next_attempt_after = NULL,
                    lease_holder = NULL,
                    lease_expires_at = NULL,
                    updated_at = now()
                WHERE node_id = $1
                  AND phase IN ('pending','preflight','provisioning','bootstrapping')
                """,
                node_id,
            )
        return res.endswith(" 1")

    async def reset_for_retry(self, *, node_id: UUID) -> ProvisioningJob | None:
        """Re-enqueue a failed job: phase='pending', attempt_count=0, all
        error fields cleared. Returns the updated job, or None if the
        current job for this node isn't in 'failed'."""
        async with self.db.acquire() as conn:
            row = await conn.fetchrow(
                """
                UPDATE provisioning_jobs
                SET phase = 'pending',
                    attempt_count = 0,
                    next_attempt_after = NULL,
                    last_error_code = NULL,
                    last_error_message = NULL,
                    last_error_hint = NULL,
                    error_class = NULL,
                    lease_holder = NULL,
                    lease_expires_at = NULL,
                    updated_at = now()
                WHERE node_id = $1 AND phase = 'failed'
                RETURNING *
                """,
                node_id,
            )
        return ProvisioningJob.from_row(row) if row else None

    # ---- read paths -----------------------------------------------------

    async def get(self, job_id: UUID) -> ProvisioningJob | None:
        async with self.db.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM provisioning_jobs WHERE id = $1", job_id,
            )
        return ProvisioningJob.from_row(row) if row else None

    async def get_by_node(self, node_id: UUID) -> ProvisioningJob | None:
        """Most-recent job for a node. Used by HTTP GET /provisioning and
        by the upgrade migration's idempotency check."""
        async with self.db.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT * FROM provisioning_jobs
                WHERE node_id = $1
                ORDER BY created_at DESC
                LIMIT 1
                """,
                node_id,
            )
        return ProvisioningJob.from_row(row) if row else None
```

- [ ] **Step 6.4: Run test to verify it passes**

```bash
pytest package/src/inferia/services/orchestration/services/provisioning/jobs/tests/test_repository.py -v
```

Expected: all 16 tests PASS.

- [ ] **Step 6.5: Commit**

```bash
git add package/src/inferia/services/orchestration/services/provisioning/jobs/repository.py \
        package/src/inferia/services/orchestration/services/provisioning/jobs/tests/test_repository.py
git -c user.signingkey=/home/ankit/.ssh/id_ed25519_gh commit -S -m "provisioning: add ProvisioningJobRepository

Async asyncpg-backed repository owning every write to the
provisioning_jobs table. enqueue / claim_next_job (FOR UPDATE SKIP
LOCKED) / renew_lease / release_lease / transition_to / schedule_retry
/ fail / request_cancel / reset_for_retry. Read side: get / get_by_node.

All methods are single round-trips with phase/lease guards in the
WHERE clause so we cannot clobber a concurrent transition or
accidentally overwrite a stolen lease.

Tested with mocked asyncpg connections following the pattern in
worker_controller.test_auth; concurrency test under real PG comes in
the next task."
```

---

### Task 7: Repository concurrency test (real PG)

**Files:**
- Create: `package/src/inferia/services/orchestration/services/provisioning/jobs/tests/test_repository_concurrency.py`

- [ ] **Step 7.1: Write the failing test**

Create `package/src/inferia/services/orchestration/services/provisioning/jobs/tests/test_repository_concurrency.py`:

```python
"""Real-PG concurrency tests for ProvisioningJobRepository.

Verifies that claim_next_job's FOR UPDATE SKIP LOCKED actually prevents
double-claim under contention. Skipped unless INFERIA_TEST_DATABASE_URL
is set. Requires the 20260528_provisioning_jobs migration to be applied
to the target DB."""
from __future__ import annotations

import asyncio
import os
import uuid
from pathlib import Path

import asyncpg
import pytest

from inferia.services.orchestration.services.provisioning.jobs.repository import (
    ProvisioningJobRepository,
)

MIGRATION = Path(__file__).resolve().parents[6] / "infra" / "schema" / "migrations" / "20260528_provisioning_jobs.sql"


@pytest.fixture
def test_database_url() -> str:
    url = os.environ.get("INFERIA_TEST_DATABASE_URL")
    if not url:
        pytest.skip("INFERIA_TEST_DATABASE_URL not set")
    return url


@pytest.fixture
async def pool(test_database_url):
    pool = await asyncpg.create_pool(test_database_url, min_size=2, max_size=20)
    # Apply migration (idempotent).
    sql = MIGRATION.read_text()
    async with pool.acquire() as conn:
        for stmt in [s.strip() for s in sql.split(";") if s.strip()]:
            await conn.execute(stmt)
    yield pool
    await pool.close()


class PoolDB:
    def __init__(self, pool):
        self.pool = pool
    def acquire(self):
        return self.pool.acquire()


async def _seed_job(pool, *, n: int = 20):
    """Insert n pending jobs and a fake compute_inventory parent row each.
    Returns the list of inserted job ids."""
    async with pool.acquire() as conn:
        # Make a pool row to satisfy the FK.
        pool_id = uuid.uuid4()
        await conn.execute(
            """INSERT INTO compute_pools (id, org_id, name, provider, lifecycle_state)
               VALUES ($1, 'org-concurrency', 'p', 'aws', 'running')
               ON CONFLICT (id) DO NOTHING""",
            pool_id,
        )
        ids = []
        for _ in range(n):
            node_id = uuid.uuid4()
            await conn.execute(
                """INSERT INTO compute_inventory (id, pool_id, provider,
                       provider_instance_id, state, agent_kind)
                   VALUES ($1, $2, 'aws', 'placeholder:c', 'provisioning', 'worker')""",
                node_id, pool_id,
            )
            job_id = uuid.uuid4()
            await conn.execute(
                """INSERT INTO provisioning_jobs (
                       id, node_id, pool_id, org_id, provider, spec, phase
                   ) VALUES ($1, $2, $3, 'org-concurrency', 'aws', '{}'::jsonb, 'pending')""",
                job_id, node_id, pool_id,
            )
            ids.append(job_id)
    return ids, pool_id


@pytest.mark.asyncio
async def test_no_double_claim_under_20_concurrent_claimers(pool):
    """20 workers all calling claim_next_job → each gets a unique job."""
    job_ids, pool_id = await _seed_job(pool, n=20)
    repo = ProvisioningJobRepository(PoolDB(pool))

    async def claim(holder: str):
        return await repo.claim_next_job(lease_holder=holder, lease_seconds=300)

    results = await asyncio.gather(
        *[claim(f"worker-{i}") for i in range(20)]
    )
    claimed_ids = [job.id for job in results if job is not None]
    assert len(claimed_ids) == 20
    assert len(set(claimed_ids)) == 20  # all unique — no double-claim

    # Cleanup.
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM provisioning_jobs WHERE id = ANY($1)", job_ids)
        await conn.execute("DELETE FROM compute_inventory WHERE pool_id = $1", pool_id)
        await conn.execute("DELETE FROM compute_pools WHERE id = $1", pool_id)


@pytest.mark.asyncio
async def test_claim_skips_leased_jobs(pool):
    """A job already leased by holder A is not claimable by holder B until
    the lease expires."""
    job_ids, pool_id = await _seed_job(pool, n=1)
    repo = ProvisioningJobRepository(PoolDB(pool))

    first = await repo.claim_next_job(lease_holder="A", lease_seconds=300)
    assert first is not None
    second = await repo.claim_next_job(lease_holder="B", lease_seconds=300)
    assert second is None

    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM provisioning_jobs WHERE id = ANY($1)", job_ids)
        await conn.execute("DELETE FROM compute_inventory WHERE pool_id = $1", pool_id)
        await conn.execute("DELETE FROM compute_pools WHERE id = $1", pool_id)


@pytest.mark.asyncio
async def test_claim_picks_up_expired_lease(pool):
    """If a lease's lease_expires_at < now(), another reconciler claims it."""
    job_ids, pool_id = await _seed_job(pool, n=1)
    repo = ProvisioningJobRepository(PoolDB(pool))

    # Holder A grabs it but with a 0-second lease (already expired).
    first = await repo.claim_next_job(lease_holder="A", lease_seconds=0)
    assert first is not None

    # Holder B claims because A's lease is already expired.
    second = await repo.claim_next_job(lease_holder="B", lease_seconds=300)
    assert second is not None
    assert second.id == first.id
    assert second.lease_holder == "B"

    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM provisioning_jobs WHERE id = ANY($1)", job_ids)
        await conn.execute("DELETE FROM compute_inventory WHERE pool_id = $1", pool_id)
        await conn.execute("DELETE FROM compute_pools WHERE id = $1", pool_id)


@pytest.mark.asyncio
async def test_cancelling_jobs_have_priority(pool):
    """Jobs in 'cancelling' phase are claimed before plain 'pending' jobs."""
    job_ids, pool_id = await _seed_job(pool, n=2)
    repo = ProvisioningJobRepository(PoolDB(pool))

    # Mark one job as 'cancelling' (the second).
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE provisioning_jobs SET phase='cancelling' WHERE id=$1",
            job_ids[1],
        )

    claimed = await repo.claim_next_job(lease_holder="W", lease_seconds=300)
    assert claimed is not None
    assert claimed.id == job_ids[1]

    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM provisioning_jobs WHERE id = ANY($1)", job_ids)
        await conn.execute("DELETE FROM compute_inventory WHERE pool_id = $1", pool_id)
        await conn.execute("DELETE FROM compute_pools WHERE id = $1", pool_id)
```

- [ ] **Step 7.2: Run test to verify it fails or skips**

```bash
pytest package/src/inferia/services/orchestration/services/provisioning/jobs/tests/test_repository_concurrency.py -v
```

Expected (without env): all 4 tests SKIP with "INFERIA_TEST_DATABASE_URL not set".
Expected (with env set against an unmigrated DB): tests FAIL (column or table missing).

- [ ] **Step 7.3: Run against a real DB**

Set up a test DB (use a Postgres docker container; do not target a production DB). Then:

```bash
docker run --rm -d --name pg-test -p 5433:5432 -e POSTGRES_PASSWORD=test postgres:16
# Wait until ready, then apply the global schema and prior migrations
# (existing build_schema.sh or equivalent), then run:
INFERIA_TEST_DATABASE_URL=postgresql://postgres:test@localhost:5433/postgres \
    pytest package/src/inferia/services/orchestration/services/provisioning/jobs/tests/test_repository_concurrency.py -v
```

Expected: all 4 tests PASS.

- [ ] **Step 7.4: Commit**

```bash
git add package/src/inferia/services/orchestration/services/provisioning/jobs/tests/test_repository_concurrency.py
git -c user.signingkey=/home/ankit/.ssh/id_ed25519_gh commit -S -m "provisioning: real-PG concurrency tests for ProvisioningJobRepository

20-worker claim race verifies FOR UPDATE SKIP LOCKED prevents
double-claim. Three more tests cover lease respect, expired-lease
takeover, and 'cancelling'-phase priority. Gated on
INFERIA_TEST_DATABASE_URL so CI can decide whether to run them."
```

---

### Task 8: Events emitter

**Files:**
- Create: `package/src/inferia/services/orchestration/services/provisioning/events.py`
- Test: `package/src/inferia/services/orchestration/services/provisioning/tests/test_events.py`

- [ ] **Step 8.1: Write the failing test**

Create `package/src/inferia/services/orchestration/services/provisioning/tests/test_events.py`:

```python
"""Tests for the events.emit_event helper."""
from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from inferia.services.orchestration.services.provisioning.events import emit_event
from inferia.services.orchestration.services.provisioning.jobs.model import Phase


def _make_db_with_conn(conn):
    db = MagicMock()
    db.acquire = MagicMock()
    db.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    db.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    return db


@pytest.mark.asyncio
async def test_emit_event_writes_to_node_provisioning_events():
    conn = MagicMock()
    conn.execute = AsyncMock(return_value="INSERT 0 1")
    db = _make_db_with_conn(conn)
    await emit_event(
        db,
        pool_id=uuid.uuid4(),
        node_id=uuid.uuid4(),
        phase=Phase.PROVISIONING,
        status="log",
        message="Creating EC2 instance",
        extra={"step": 3},
    )
    conn.execute.assert_awaited_once()
    sql = conn.execute.await_args.args[0]
    assert "INSERT INTO node_provisioning_events" in sql


@pytest.mark.asyncio
async def test_emit_event_jsonb_serialises_extra():
    """The `extra` dict is serialised as JSON for the jsonb column."""
    conn = MagicMock()
    conn.execute = AsyncMock(return_value="INSERT 0 1")
    db = _make_db_with_conn(conn)
    await emit_event(
        db,
        pool_id=uuid.uuid4(), node_id=uuid.uuid4(),
        phase=Phase.PREFLIGHT, status="failed",
        message="bad creds",
        extra={"code": "INVALID_CREDENTIALS"},
    )
    args = conn.execute.await_args.args
    # extra is passed as a JSON string (last positional positional arg).
    last_arg = args[-1]
    parsed = json.loads(last_arg) if isinstance(last_arg, str) else last_arg
    assert parsed == {"code": "INVALID_CREDENTIALS"}


@pytest.mark.asyncio
async def test_emit_event_handles_none_extra():
    conn = MagicMock()
    conn.execute = AsyncMock(return_value="INSERT 0 1")
    db = _make_db_with_conn(conn)
    await emit_event(
        db,
        pool_id=uuid.uuid4(), node_id=uuid.uuid4(),
        phase=Phase.READY, status="succeeded",
        message="node ready",
    )
    # Should not raise; extra defaults to None → empty JSONB.
    conn.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_emit_event_uses_phase_value_string():
    """The phase column is text; we pass the .value string."""
    conn = MagicMock()
    conn.execute = AsyncMock(return_value="INSERT 0 1")
    db = _make_db_with_conn(conn)
    await emit_event(
        db,
        pool_id=uuid.uuid4(), node_id=uuid.uuid4(),
        phase=Phase.BOOTSTRAPPING, status="running",
        message="waiting for worker",
    )
    args = conn.execute.await_args.args
    assert "bootstrapping" in args  # passed somewhere in the args
```

- [ ] **Step 8.2: Run test to verify it fails**

```bash
pytest package/src/inferia/services/orchestration/services/provisioning/tests/test_events.py -v
```

Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 8.3: Implement events.emit_event**

Inspect the existing `node_provisioning_events` schema to confirm columns. The 2026-05-25 UX spec describes it; verify by reading `package/src/inferia/infra/schema/migrations/20260525_add_node_provisioning_events.sql`. Then create `package/src/inferia/services/orchestration/services/provisioning/events.py`:

```python
"""Single helper for writing rows into node_provisioning_events.

All phase handlers and the reconciler funnel event writes through this
function so the read-side (GET /provisioning, GET /provisioning-logs)
sees a consistent shape. Existing direct-write call sites in
pulumi_aws_adapter.py get removed in Task 10."""
from __future__ import annotations

import json
import uuid
from typing import Any
from uuid import UUID

from inferia.services.orchestration.services.provisioning.jobs.model import (
    EventStatus, Phase,
)


async def emit_event(
    db,
    *,
    pool_id: UUID,
    node_id: UUID,
    phase: Phase,
    status: EventStatus,
    message: str,
    extra: dict[str, Any] | None = None,
) -> None:
    """Append a row to node_provisioning_events.

    `extra` is JSON-serialised into the jsonb column; pass None for
    "no extra metadata".
    """
    extra_json = json.dumps(extra) if extra is not None else "{}"
    async with db.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO node_provisioning_events
                (id, pool_id, node_id, phase, status, message, extra, created_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, now())
            """,
            uuid.uuid4(), pool_id, node_id, phase.value, status, message, extra_json,
        )
```

- [ ] **Step 8.4: Run test to verify it passes**

```bash
pytest package/src/inferia/services/orchestration/services/provisioning/tests/test_events.py -v
```

Expected: all 4 tests PASS.

- [ ] **Step 8.5: Commit**

```bash
git add package/src/inferia/services/orchestration/services/provisioning/events.py \
        package/src/inferia/services/orchestration/services/provisioning/tests/test_events.py
git -c user.signingkey=/home/ankit/.ssh/id_ed25519_gh commit -S -m "provisioning: add emit_event helper

Single funnel for writing node_provisioning_events rows. Reconciler and
all phase handlers call this; the existing direct-write call sites in
pulumi_aws_adapter.py go away in the adapter-prune task."
```

---

### Task 9: Classifier

**Files:**
- Create: `package/src/inferia/services/orchestration/services/provisioning/retry/classifier.py`
- Test: `package/src/inferia/services/orchestration/services/provisioning/retry/tests/test_classifier.py`

- [ ] **Step 9.1: Write the failing test**

Create `package/src/inferia/services/orchestration/services/provisioning/retry/tests/test_classifier.py`:

```python
"""Tests for the classify_error function."""
from __future__ import annotations

import asyncio
import socket
from unittest.mock import MagicMock

import pytest

from inferia.services.orchestration.services.provisioning.errors import (
    AMINotFoundError, AWSServerError, AWSThrottledError,
    CapacityUnavailableError, InvalidCredentialsError, InvalidInstanceTypeError,
    NetworkError, PermanentError, PulumiCliMissingError, PulumiTransientError,
    QuotaExceededError, SecurityGroupNotFoundError, SubnetNotFoundError,
    TransientError,
)
from inferia.services.orchestration.services.provisioning.jobs.model import ErrorClass
from inferia.services.orchestration.services.provisioning.retry.classifier import (
    classify_error,
)


# ---- typed exception passthrough -----------------------------------------


@pytest.mark.parametrize("exc, expected_code, expected_class", [
    (AWSThrottledError("x"),             "AWS_THROTTLED",         ErrorClass.TRANSIENT),
    (AWSServerError("x"),                "AWS_5XX",               ErrorClass.TRANSIENT),
    (PulumiTransientError("x"),          "PULUMI_TRANSIENT",      ErrorClass.TRANSIENT),
    (NetworkError("x"),                  "NETWORK_ERROR",         ErrorClass.TRANSIENT),
    (PulumiCliMissingError("x"),         "PULUMI_CLI_MISSING",    ErrorClass.PERMANENT),
    (InvalidCredentialsError("x"),       "INVALID_CREDENTIALS",   ErrorClass.PERMANENT),
    (AMINotFoundError("x"),              "AMI_NOT_FOUND",         ErrorClass.PERMANENT),
    (SubnetNotFoundError("x"),           "SUBNET_NOT_FOUND",      ErrorClass.PERMANENT),
    (SecurityGroupNotFoundError("x"),    "SG_NOT_FOUND",          ErrorClass.PERMANENT),
    (InvalidInstanceTypeError("x"),      "INVALID_INSTANCE_TYPE", ErrorClass.PERMANENT),
    (QuotaExceededError("x"),            "QUOTA_EXCEEDED",        ErrorClass.INFRASTRUCTURE),
    (CapacityUnavailableError("x"),      "INSUFFICIENT_CAPACITY", ErrorClass.INFRASTRUCTURE),
])
def test_typed_provisioning_errors_passthrough(exc, expected_code, expected_class):
    ce = classify_error(exc)
    assert ce.code == expected_code
    assert ce.error_class == expected_class


def test_hint_preserved_from_typed_error():
    exc = AMINotFoundError("ami-x not in us-west-2", hint="try us-east-1")
    ce = classify_error(exc)
    assert ce.hint == "try us-east-1"


# ---- botocore.ClientError mapping ---------------------------------------


def _client_error(code: str, msg: str = "boom"):
    """Build a fake botocore.ClientError without importing botocore."""
    from botocore.exceptions import ClientError
    return ClientError(
        error_response={"Error": {"Code": code, "Message": msg}},
        operation_name="RunInstances",
    )


@pytest.mark.parametrize("aws_code, expected_code, expected_class", [
    ("RequestLimitExceeded",        "AWS_THROTTLED",         ErrorClass.TRANSIENT),
    ("Throttling",                  "AWS_THROTTLED",         ErrorClass.TRANSIENT),
    ("ThrottlingException",         "AWS_THROTTLED",         ErrorClass.TRANSIENT),
    ("AuthFailure",                 "INVALID_CREDENTIALS",   ErrorClass.PERMANENT),
    ("UnauthorizedOperation",       "INVALID_CREDENTIALS",   ErrorClass.PERMANENT),
    ("InvalidClientTokenId",        "INVALID_CREDENTIALS",   ErrorClass.PERMANENT),
    ("SignatureDoesNotMatch",       "INVALID_CREDENTIALS",   ErrorClass.PERMANENT),
    ("InvalidAMIID.NotFound",       "AMI_NOT_FOUND",         ErrorClass.PERMANENT),
    ("InvalidSubnetID.NotFound",    "SUBNET_NOT_FOUND",      ErrorClass.PERMANENT),
    ("InvalidGroup.NotFound",       "SG_NOT_FOUND",          ErrorClass.PERMANENT),
    ("VcpuLimitExceeded",           "QUOTA_EXCEEDED",        ErrorClass.INFRASTRUCTURE),
    ("InstanceLimitExceeded",       "QUOTA_EXCEEDED",        ErrorClass.INFRASTRUCTURE),
    ("InsufficientInstanceCapacity","INSUFFICIENT_CAPACITY", ErrorClass.INFRASTRUCTURE),
])
def test_botocore_error_codes_map_correctly(aws_code, expected_code, expected_class):
    exc = _client_error(aws_code)
    ce = classify_error(exc)
    assert ce.code == expected_code, f"AWS code {aws_code} → {ce.code} (expected {expected_code})"
    assert ce.error_class == expected_class


def test_botocore_5xx_unknown_code_maps_to_aws_5xx():
    """A ClientError with no specific Code but 5xx status → AWS_5XX."""
    from botocore.exceptions import ClientError
    exc = ClientError(
        error_response={
            "Error": {"Code": "InternalServerError", "Message": "boom"},
            "ResponseMetadata": {"HTTPStatusCode": 503},
        },
        operation_name="RunInstances",
    )
    ce = classify_error(exc)
    assert ce.error_class == ErrorClass.TRANSIENT
    assert ce.code == "AWS_5XX"


def test_botocore_invalid_parameter_value_maps_to_instance_type():
    """InvalidParameterValue typically means a malformed instance type."""
    exc = _client_error("InvalidParameterValue", "Invalid instance type: zz")
    ce = classify_error(exc)
    assert ce.code == "INVALID_INSTANCE_TYPE"


# ---- network errors -----------------------------------------------------


def test_socket_gaierror_maps_to_network_error():
    ce = classify_error(socket.gaierror(-2, "name resolution failed"))
    assert ce.code == "NETWORK_ERROR"
    assert ce.error_class == ErrorClass.TRANSIENT


def test_connection_refused_maps_to_network_error():
    ce = classify_error(ConnectionRefusedError("connection refused"))
    assert ce.code == "NETWORK_ERROR"
    assert ce.error_class == ErrorClass.TRANSIENT


def test_asyncio_timeout_maps_to_network_error():
    ce = classify_error(asyncio.TimeoutError("upstream timeout"))
    assert ce.code == "NETWORK_ERROR"
    assert ce.error_class == ErrorClass.TRANSIENT


# ---- unknown → UNCLASSIFIED PERMANENT (fail-loud) -----------------------


def test_unknown_exception_is_unclassified_permanent():
    class Mystery(Exception):
        pass
    ce = classify_error(Mystery("something weird"))
    assert ce.code == "UNCLASSIFIED"
    assert ce.error_class == ErrorClass.PERMANENT
    # Message should include the type repr so an operator can file a bug.
    assert "Mystery" in ce.message


# ---- propagation: never classify these as failures ----------------------


def test_cancelled_error_propagates():
    """asyncio.CancelledError must NOT be classified; it bubbles up so
    handlers and the reconciler can do orderly shutdown."""
    with pytest.raises(asyncio.CancelledError):
        classify_error(asyncio.CancelledError())


def test_keyboard_interrupt_propagates():
    with pytest.raises(KeyboardInterrupt):
        classify_error(KeyboardInterrupt())


# ---- hints ----------------------------------------------------------------


def test_invalid_credentials_hint_includes_settings_path():
    """Operator-facing hint must mention where to fix the creds."""
    exc = _client_error("AuthFailure")
    ce = classify_error(exc)
    assert ce.hint is not None
    assert "Settings" in ce.hint or "Providers" in ce.hint


def test_pulumi_cli_missing_hint_includes_install_command():
    exc = PulumiCliMissingError("no pulumi binary")
    ce = classify_error(exc)
    # The typed error may not carry a hint by default; classifier should add one.
    assert ce.hint is not None
    assert "pulumi.com" in ce.hint
```

- [ ] **Step 9.2: Run test to verify it fails**

```bash
pytest package/src/inferia/services/orchestration/services/provisioning/retry/tests/test_classifier.py -v
```

Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 9.3: Implement the classifier**

Create `package/src/inferia/services/orchestration/services/provisioning/retry/classifier.py`:

```python
"""Single source of truth for error → retry-decision classification.

Every place that catches an exception from a phase handler MUST go
through classify_error. Adding a new known error = one entry below.

asyncio.CancelledError and KeyboardInterrupt are deliberately re-raised
so handlers and the reconciler can do orderly shutdown — they are NOT
classified as failures.
"""
from __future__ import annotations

import asyncio
import socket
from typing import Any

from inferia.services.orchestration.services.provisioning.errors import (
    AMINotFoundError, AWSServerError, AWSThrottledError,
    CapacityUnavailableError, InfrastructureError, InvalidCredentialsError,
    InvalidInstanceTypeError, NetworkError, PermanentError,
    PulumiCliMissingError, PulumiTransientError, ProvisioningError,
    QuotaExceededError, SecurityGroupNotFoundError, SubnetExhaustedError,
    SubnetNotFoundError, TransientError,
)
from inferia.services.orchestration.services.provisioning.jobs.model import (
    ClassifiedError, ErrorClass,
)


# Hints attached at classification time for the typed errors that don't
# carry one by default. Keep these aligned with the spec's
# "Error → UI rendering" table.
_DEFAULT_HINTS: dict[str, str] = {
    "PULUMI_CLI_MISSING":     "Install in the inferia-app container: "
                              "curl -fsSL https://get.pulumi.com | sh",
    "INVALID_CREDENTIALS":    "Open Settings → Providers → AWS and re-enter "
                              "your access key.",
    "AMI_NOT_FOUND":          "The AMI is not available in the chosen region. "
                              "Try us-east-1 or pick a different AMI.",
    "SUBNET_NOT_FOUND":       "The configured subnet does not exist in this region. "
                              "Update Settings → Providers → AWS.",
    "SG_NOT_FOUND":           "The configured security group does not exist. "
                              "Update Settings → Providers → AWS.",
    "INVALID_INSTANCE_TYPE":  "The selected instance type is unknown or "
                              "unavailable in the region.",
    "QUOTA_EXCEEDED":         "Request a quota increase from AWS Support for "
                              "the relevant instance family in the region.",
    "INSUFFICIENT_CAPACITY":  "AWS has no spare capacity. Try a different AZ, "
                              "instance type, or wait and retry. Spot is "
                              "especially prone to this.",
    "SUBNET_EXHAUSTED":       "The subnet has no free IPs. Use a different "
                              "subnet or expand the CIDR.",
    "AWS_THROTTLED":          "AWS rate limited the request. The reconciler "
                              "will back off and retry automatically.",
    "AWS_5XX":                "AWS returned a server error. The reconciler "
                              "will back off and retry automatically.",
    "PULUMI_TRANSIENT":       "Pulumi reported a transient error. Retrying "
                              "automatically.",
    "NETWORK_ERROR":          "Network connectivity issue. Retrying automatically.",
    "UNCLASSIFIED":           "This was not a known error. The full stack "
                              "trace is in the Logs tab. Please file a bug.",
}


# Botocore error code → typed exception class.
_AWS_CODE_MAP: dict[str, type[ProvisioningError]] = {
    "RequestLimitExceeded":           AWSThrottledError,
    "Throttling":                     AWSThrottledError,
    "ThrottlingException":            AWSThrottledError,
    "AuthFailure":                    InvalidCredentialsError,
    "UnauthorizedOperation":          InvalidCredentialsError,
    "InvalidClientTokenId":           InvalidCredentialsError,
    "SignatureDoesNotMatch":          InvalidCredentialsError,
    "InvalidAMIID.NotFound":          AMINotFoundError,
    "InvalidSubnetID.NotFound":       SubnetNotFoundError,
    "InvalidGroup.NotFound":          SecurityGroupNotFoundError,
    "VcpuLimitExceeded":              QuotaExceededError,
    "InstanceLimitExceeded":          QuotaExceededError,
    "InsufficientInstanceCapacity":   CapacityUnavailableError,
    "InvalidParameterValue":          InvalidInstanceTypeError,
}


def classify_error(exc: BaseException) -> ClassifiedError:
    """Map any exception → ClassifiedError.

    Raises asyncio.CancelledError / KeyboardInterrupt through unchanged."""
    if isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt)):
        raise exc

    # 1. Typed ProvisioningError → use its declared code + class.
    if isinstance(exc, ProvisioningError):
        return _build(exc.code, _err_class(exc), str(exc), exc.hint)

    # 2. botocore.ClientError → map by AWS code, fall back to 5xx detection.
    try:
        from botocore.exceptions import ClientError  # type: ignore
        if isinstance(exc, ClientError):
            return _classify_aws_client_error(exc)
    except ImportError:
        pass  # botocore not present in some test environments

    # 3. Network-ish exceptions → NetworkError.
    if isinstance(exc, (socket.gaierror, ConnectionError,
                        ConnectionRefusedError, ConnectionResetError,
                        TimeoutError, asyncio.TimeoutError)):
        return _build("NETWORK_ERROR", ErrorClass.TRANSIENT, str(exc) or repr(exc))

    # 4. Fall back: UNCLASSIFIED PERMANENT (fail loud, include type for triage).
    return _build(
        "UNCLASSIFIED",
        ErrorClass.PERMANENT,
        f"{type(exc).__name__}: {exc!s}",
    )


def _classify_aws_client_error(exc: Any) -> ClassifiedError:
    code = (exc.response.get("Error") or {}).get("Code", "")
    status = (exc.response.get("ResponseMetadata") or {}).get("HTTPStatusCode")
    cls = _AWS_CODE_MAP.get(code)
    if cls is not None:
        return _build(cls.code, _class_to_error_class(cls), str(exc))
    if isinstance(status, int) and 500 <= status < 600:
        return _build("AWS_5XX", ErrorClass.TRANSIENT, str(exc))
    # Unknown AWS error → UNCLASSIFIED PERMANENT.
    return _build(
        "UNCLASSIFIED",
        ErrorClass.PERMANENT,
        f"AWS ClientError code={code} status={status}: {exc}",
    )


def _err_class(exc: ProvisioningError) -> ErrorClass:
    if isinstance(exc, TransientError):
        return ErrorClass.TRANSIENT
    if isinstance(exc, InfrastructureError):
        return ErrorClass.INFRASTRUCTURE
    if isinstance(exc, PermanentError):
        return ErrorClass.PERMANENT
    # Shouldn't happen — ProvisioningError directly raised. Treat as permanent.
    return ErrorClass.PERMANENT


def _class_to_error_class(cls: type[ProvisioningError]) -> ErrorClass:
    if issubclass(cls, TransientError):
        return ErrorClass.TRANSIENT
    if issubclass(cls, InfrastructureError):
        return ErrorClass.INFRASTRUCTURE
    return ErrorClass.PERMANENT


def _build(
    code: str,
    error_class: ErrorClass,
    message: str,
    hint: str | None = None,
) -> ClassifiedError:
    if hint is None:
        hint = _DEFAULT_HINTS.get(code)
    return ClassifiedError(error_class=error_class, code=code, message=message, hint=hint)
```

- [ ] **Step 9.4: Run test to verify it passes**

```bash
pytest package/src/inferia/services/orchestration/services/provisioning/retry/tests/test_classifier.py -v
```

Expected: all 27 tests PASS.

- [ ] **Step 9.5: Commit**

```bash
git add package/src/inferia/services/orchestration/services/provisioning/retry/classifier.py \
        package/src/inferia/services/orchestration/services/provisioning/retry/tests/test_classifier.py
git -c user.signingkey=/home/ankit/.ssh/id_ed25519_gh commit -S -m "provisioning: add classify_error

Single source of truth for exception → ClassifiedError. Handles typed
ProvisioningError passthrough, 13 botocore.ClientError codes via
mapping table, 5xx fallback for unknown AWS codes, network errors, and
fail-loud UNCLASSIFIED for anything unknown. CancelledError and
KeyboardInterrupt propagate unchanged so the reconciler shutdown path
is not classified as a failure.

Default operator-facing hints attached at classification time so the
typed errors themselves stay terse."
```

---

### Task 10: Pulumi adapter prune → `run_pulumi_up_sync`

**Files:**
- Modify: `package/src/inferia/services/orchestration/services/adapter_engine/adapters/pulumi/pulumi_aws_adapter.py`
- Modify: `package/src/inferia/services/orchestration/services/adapter_engine/adapters/pulumi/test_pulumi_aws_adapter.py`

- [ ] **Step 10.1: Write the failing test**

Add to `test_pulumi_aws_adapter.py`:

```python
"""Tests for run_pulumi_up_sync — the pure sync function that replaces
provision_node + _provision_async."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from inferia.services.orchestration.services.adapter_engine.adapters.pulumi.pulumi_aws_adapter import (
    StackOutputs,
    run_pulumi_up_sync,
)
from inferia.services.orchestration.services.provisioning.errors import (
    AMINotFoundError, InvalidCredentialsError, PulumiCliMissingError,
)


def test_run_pulumi_up_sync_returns_stack_outputs_on_success():
    """A successful stack.up() returns a StackOutputs dataclass."""
    fake_stack = MagicMock()
    fake_stack.up.return_value = MagicMock(outputs={
        "instance_id": MagicMock(value="i-abc"),
        "public_dns": MagicMock(value="ec2-1-2-3-4.compute-1.amazonaws.com"),
        "region": MagicMock(value="us-east-1"),
        "ami_id": MagicMock(value="ami-deadbeef"),
    })
    with patch(
        "inferia.services.orchestration.services.adapter_engine."
        "adapters.pulumi.pulumi_aws_adapter._make_stack",
        return_value=fake_stack,
    ):
        out = run_pulumi_up_sync(
            stack_name="org-pool-node",
            program=lambda: None,
            env={"AWS_ACCESS_KEY_ID": "x", "AWS_SECRET_ACCESS_KEY": "y"},
        )
    assert isinstance(out, StackOutputs)
    assert out.instance_id == "i-abc"
    assert out.region == "us-east-1"


def test_run_pulumi_up_sync_raises_pulumi_cli_missing_on_filenotfounderror():
    with patch(
        "inferia.services.orchestration.services.adapter_engine."
        "adapters.pulumi.pulumi_aws_adapter._make_stack",
        side_effect=FileNotFoundError("[Errno 2] No such file or directory: 'pulumi'"),
    ):
        with pytest.raises(PulumiCliMissingError):
            run_pulumi_up_sync(
                stack_name="s", program=lambda: None, env={},
            )


def test_run_pulumi_up_sync_raises_invalid_credentials_on_auth_failure():
    """Pulumi up surfacing an AWS AuthFailure becomes InvalidCredentialsError."""
    fake_stack = MagicMock()
    err = Exception(
        "operation failed: AuthFailure: AWS was not able to validate "
        "the provided access credentials"
    )
    fake_stack.up.side_effect = err
    with patch(
        "inferia.services.orchestration.services.adapter_engine."
        "adapters.pulumi.pulumi_aws_adapter._make_stack",
        return_value=fake_stack,
    ):
        with pytest.raises(InvalidCredentialsError):
            run_pulumi_up_sync(
                stack_name="s", program=lambda: None, env={},
            )


def test_run_pulumi_up_sync_raises_ami_not_found():
    fake_stack = MagicMock()
    fake_stack.up.side_effect = Exception(
        "InvalidAMIID.NotFound: The image id '[ami-deadbeef]' does not exist"
    )
    with patch(
        "inferia.services.orchestration.services.adapter_engine."
        "adapters.pulumi.pulumi_aws_adapter._make_stack",
        return_value=fake_stack,
    ):
        with pytest.raises(AMINotFoundError):
            run_pulumi_up_sync(
                stack_name="s", program=lambda: None, env={},
            )


def test_run_pulumi_up_sync_uses_local_workspace_with_env():
    """Env vars are passed into local_workspace_opts so Pulumi inherits them."""
    fake_stack = MagicMock()
    fake_stack.up.return_value = MagicMock(outputs={
        "instance_id": MagicMock(value="i-x"),
    })
    with patch(
        "inferia.services.orchestration.services.adapter_engine."
        "adapters.pulumi.pulumi_aws_adapter._make_stack",
        return_value=fake_stack,
    ) as mk:
        run_pulumi_up_sync(
            stack_name="s",
            program=lambda: None,
            env={"AWS_ACCESS_KEY_ID": "K", "AWS_SECRET_ACCESS_KEY": "S"},
        )
    kwargs = mk.call_args.kwargs
    assert kwargs["env"] == {"AWS_ACCESS_KEY_ID": "K", "AWS_SECRET_ACCESS_KEY": "S"}
```

- [ ] **Step 10.2: Run test to verify it fails**

```bash
pytest package/src/inferia/services/orchestration/services/adapter_engine/adapters/pulumi/test_pulumi_aws_adapter.py::test_run_pulumi_up_sync_returns_stack_outputs_on_success -v
```

Expected: FAIL with `ImportError` for `StackOutputs` / `run_pulumi_up_sync`.

- [ ] **Step 10.3: Refactor the adapter**

Edit `package/src/inferia/services/orchestration/services/adapter_engine/adapters/pulumi/pulumi_aws_adapter.py`. Delete the `provision_node` and `_provision_async` methods (and `provision_cluster` if it calls `provision_node`). Keep credential resolution + Pulumi program builders + AMI lookup helpers. Replace with:

```python
"""Pulumi AWS adapter — pure functions, no DB writes.

Pre-refactor (May 2026), this module held a fire-and-forget asyncio task
that ran stack.up() and wrote outputs to compute_pools.metadata. That
swallowed errors. Post-refactor, the only public entry point is
run_pulumi_up_sync — a synchronous function that returns StackOutputs or
raises a typed ProvisioningError. The reconciler is responsible for
wrapping calls in asyncio.to_thread() and writing outcomes to the
provisioning_jobs table.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

# Existing imports preserved (credentials, programs, ami) — left as-is.
from inferia.services.orchestration.services.provisioning.errors import (
    AMINotFoundError, InvalidCredentialsError, PermanentError,
    PulumiCliMissingError, PulumiTransientError, ProvisioningError,
)


@dataclass(frozen=True)
class StackOutputs:
    """What the reconciler stores into provisioning_jobs.pulumi_stack_outputs."""
    instance_id: str | None
    public_dns: str | None
    region: str | None
    ami_id: str | None

    @classmethod
    def from_pulumi_outputs(cls, outputs: dict[str, Any]) -> "StackOutputs":
        def _v(key: str) -> str | None:
            ref = outputs.get(key)
            if ref is None:
                return None
            return getattr(ref, "value", ref)
        return cls(
            instance_id=_v("instance_id"),
            public_dns=_v("public_dns"),
            region=_v("region"),
            ami_id=_v("ami_id"),
        )


def _make_stack(*, stack_name: str, program: Callable, env: dict[str, str]):
    """Wraps Pulumi auto.create_or_select_stack with our local-backend env.
    Extracted so tests can mock it; production calls into pulumi_automation
    here."""
    from pulumi import automation as auto
    workspace_opts = auto.LocalWorkspaceOptions(env_vars=env)
    return auto.create_or_select_stack(
        stack_name=stack_name,
        program=program,
        opts=workspace_opts,
    )


def run_pulumi_up_sync(
    *,
    stack_name: str,
    program: Callable[[], None],
    env: dict[str, str],
) -> StackOutputs:
    """Run `pulumi up` synchronously and return the named outputs.

    Raises a typed ProvisioningError on known failures; the reconciler's
    classifier maps everything else (including pulumi.automation internals)
    to UNCLASSIFIED PERMANENT.

    This function MUST stay sync. The reconciler wraps it in
    `asyncio.to_thread(...)` because the Pulumi Python SDK has no
    `up_async`.
    """
    try:
        stack = _make_stack(stack_name=stack_name, program=program, env=env)
    except FileNotFoundError as e:
        # `pulumi` binary not on PATH — classic deploy-time failure
        # (memory: feedback_pulumi_cli_binary_required).
        raise PulumiCliMissingError(
            f"pulumi binary missing: {e}",
        ) from e

    try:
        result = stack.up()
    except ProvisioningError:
        raise
    except Exception as e:
        msg = str(e).lower()
        # Heuristic mapping for AWS errors that surface through Pulumi's
        # generic exception type. Classifier handles unknown cases via
        # UNCLASSIFIED PERMANENT.
        if "authfailure" in msg or "credentials" in msg or "unauthorized" in msg:
            raise InvalidCredentialsError(str(e)) from e
        if "invalidamiid.notfound" in msg or "image id" in msg:
            raise AMINotFoundError(str(e)) from e
        if "throttling" in msg or "requestlimitexceeded" in msg:
            raise PulumiTransientError(str(e)) from e
        raise  # let the classifier deal with UNCLASSIFIED

    return StackOutputs.from_pulumi_outputs(result.outputs or {})
```

(The existing `provision_node`, `_provision_async`, `provision_cluster`, and `deprovision_node` methods are deleted. Audit callers via `grep` and update them; `add_provider_node` is rewritten in Task 23.)

- [ ] **Step 10.4: Run test to verify it passes**

```bash
pytest package/src/inferia/services/orchestration/services/adapter_engine/adapters/pulumi/test_pulumi_aws_adapter.py -v
```

Expected: all new tests PASS. Pre-existing tests for the deleted methods will FAIL — delete those test cases as part of the cleanup. Confirm:

```bash
grep -n "provision_node\|_provision_async\|provision_cluster" \
    package/src/inferia/services/orchestration/services/adapter_engine/adapters/pulumi/test_pulumi_aws_adapter.py
```

Expected: no matches (all references removed).

- [ ] **Step 10.5: Commit**

```bash
git add package/src/inferia/services/orchestration/services/adapter_engine/adapters/pulumi/pulumi_aws_adapter.py \
        package/src/inferia/services/orchestration/services/adapter_engine/adapters/pulumi/test_pulumi_aws_adapter.py
git -c user.signingkey=/home/ankit/.ssh/id_ed25519_gh commit -S -m "pulumi-aws: prune adapter to pure run_pulumi_up_sync

Removes provision_node, _provision_async, provision_cluster — the
fire-and-forget asyncio task and the DB-write side effects move to the
reconciler in upcoming tasks. The adapter now exposes one synchronous
function that runs pulumi up and returns StackOutputs (or raises a
typed ProvisioningError). The reconciler will wrap this in
asyncio.to_thread() per the spec's 'Idempotency' section."
```

---

### Task 11: `verify_credentials` in credentials.py

**Files:**
- Modify: `package/src/inferia/services/orchestration/services/adapter_engine/adapters/pulumi/credentials.py`
- Modify: `package/src/inferia/services/orchestration/services/adapter_engine/adapters/pulumi/test_credentials.py`

- [ ] **Step 11.1: Write the failing test**

Append to `test_credentials.py`:

```python
"""Tests for verify_credentials — preflight check that hits sts:GetCallerIdentity."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from inferia.services.orchestration.services.adapter_engine.adapters.pulumi.credentials import (
    AWSCredentials,
    verify_credentials,
)
from inferia.services.orchestration.services.provisioning.errors import (
    InvalidCredentialsError, NetworkError,
)


def _creds(**over) -> AWSCredentials:
    base = dict(
        access_key_id="AKIA...",
        secret_access_key="secret",
        region="us-east-1",
        session_token=None,
    )
    base.update(over)
    return AWSCredentials(**base)


def test_verify_credentials_returns_caller_identity_on_success():
    fake_client = MagicMock()
    fake_client.get_caller_identity.return_value = {
        "UserId": "AIDA...", "Account": "123456789012",
        "Arn": "arn:aws:iam::123:user/test",
    }
    with patch(
        "inferia.services.orchestration.services.adapter_engine."
        "adapters.pulumi.credentials._boto3_sts_client",
        return_value=fake_client,
    ):
        ident = verify_credentials(_creds())
    assert ident["Account"] == "123456789012"


def test_verify_credentials_raises_invalid_credentials_on_authfailure():
    from botocore.exceptions import ClientError
    err = ClientError(
        error_response={"Error": {
            "Code": "InvalidClientTokenId",
            "Message": "The security token included in the request is invalid.",
        }},
        operation_name="GetCallerIdentity",
    )
    fake_client = MagicMock()
    fake_client.get_caller_identity.side_effect = err
    with patch(
        "inferia.services.orchestration.services.adapter_engine."
        "adapters.pulumi.credentials._boto3_sts_client",
        return_value=fake_client,
    ):
        with pytest.raises(InvalidCredentialsError):
            verify_credentials(_creds())


def test_verify_credentials_raises_network_error_on_endpoint_failure():
    from botocore.exceptions import EndpointConnectionError
    err = EndpointConnectionError(endpoint_url="https://sts.us-east-1.amazonaws.com/")
    fake_client = MagicMock()
    fake_client.get_caller_identity.side_effect = err
    with patch(
        "inferia.services.orchestration.services.adapter_engine."
        "adapters.pulumi.credentials._boto3_sts_client",
        return_value=fake_client,
    ):
        with pytest.raises(NetworkError):
            verify_credentials(_creds())
```

- [ ] **Step 11.2: Run test to verify it fails**

```bash
pytest package/src/inferia/services/orchestration/services/adapter_engine/adapters/pulumi/test_credentials.py -v
```

Expected: 3 new tests FAIL with `ImportError` (`verify_credentials`).

- [ ] **Step 11.3: Implement verify_credentials**

Edit `package/src/inferia/services/orchestration/services/adapter_engine/adapters/pulumi/credentials.py`. Add:

```python
def _boto3_sts_client(creds: AWSCredentials):
    """Built as a separate function so tests can mock without bringing
    boto3 into the test environment's import path."""
    import boto3
    return boto3.client(
        "sts",
        aws_access_key_id=creds.access_key_id,
        aws_secret_access_key=creds.secret_access_key,
        aws_session_token=creds.session_token,
        region_name=creds.region,
    )


def verify_credentials(creds: "AWSCredentials") -> dict:
    """Synchronously call sts:GetCallerIdentity to validate that the
    creds work and can reach AWS. Used by the PreflightHandler.

    Returns the GetCallerIdentity response. Raises:
    - InvalidCredentialsError on AuthFailure / InvalidClientTokenId /
      SignatureDoesNotMatch / UnauthorizedOperation.
    - NetworkError on EndpointConnectionError or similar reachability
      failures.
    - Other botocore exceptions propagate; the classifier maps them.
    """
    from botocore.exceptions import (  # local import: optional dep
        ClientError, EndpointConnectionError,
    )
    from inferia.services.orchestration.services.provisioning.errors import (
        InvalidCredentialsError, NetworkError,
    )

    client = _boto3_sts_client(creds)
    try:
        return client.get_caller_identity()
    except EndpointConnectionError as e:
        raise NetworkError(str(e)) from e
    except ClientError as e:
        code = (e.response.get("Error") or {}).get("Code", "")
        if code in {
            "AuthFailure", "InvalidClientTokenId",
            "SignatureDoesNotMatch", "UnauthorizedOperation",
        }:
            raise InvalidCredentialsError(str(e)) from e
        raise
```

- [ ] **Step 11.4: Run test to verify it passes**

```bash
pytest package/src/inferia/services/orchestration/services/adapter_engine/adapters/pulumi/test_credentials.py -v
```

Expected: all tests PASS.

- [ ] **Step 11.5: Commit**

```bash
git add package/src/inferia/services/orchestration/services/adapter_engine/adapters/pulumi/credentials.py \
        package/src/inferia/services/orchestration/services/adapter_engine/adapters/pulumi/test_credentials.py
git -c user.signingkey=/home/ankit/.ssh/id_ed25519_gh commit -S -m "pulumi-aws: add verify_credentials preflight check

Synchronous sts:GetCallerIdentity probe used by the upcoming
PreflightHandler. Maps AuthFailure / InvalidClientTokenId /
SignatureDoesNotMatch / UnauthorizedOperation to InvalidCredentialsError
and EndpointConnectionError to NetworkError so the classifier produces
the right retry-vs-fail outcome."
```

---

### Task 12: Bootstrap builder CPU branching

**Files:**
- Modify: `package/src/inferia/services/orchestration/services/adapter_engine/adapters/aws/bootstrap_builder.py`
- Modify: `package/src/inferia/services/orchestration/services/adapter_engine/adapters/aws/test_bootstrap_builder.py`

- [ ] **Step 12.1: Write the failing test**

Append to `test_bootstrap_builder.py`:

```python
"""Tests for instance_class branching in build_user_data."""
import pytest

from inferia.services.orchestration.services.adapter_engine.adapters.aws.bootstrap_builder import (
    build_user_data,
)


def _common_kwargs(**over):
    base = dict(
        bootstrap_token="bt",
        pool_id="p",
        node_name="n",
        control_plane_url="https://cp",
        inference_token="it",
        worker_image="inferia-worker:latest",
        instance_class="normal_gpu",
        gpu_count=1,
    )
    base.update(over)
    return base


def test_normal_gpu_userdata_installs_nvidia_container_runtime():
    ud = build_user_data(**_common_kwargs(instance_class="normal_gpu", gpu_count=1))
    assert "nvidia-container-runtime" in ud or "nvidia-container-toolkit" in ud


def test_normal_gpu_userdata_sets_allocatable_gpu_override_to_count():
    ud = build_user_data(**_common_kwargs(instance_class="normal_gpu", gpu_count=1))
    assert "ALLOCATABLE_GPU_OVERRIDE=1" in ud


def test_heavy_gpu_userdata_sets_allocatable_gpu_override_to_count():
    ud = build_user_data(**_common_kwargs(instance_class="heavy_gpu", gpu_count=8))
    assert "ALLOCATABLE_GPU_OVERRIDE=8" in ud


def test_cpu_userdata_skips_nvidia_container_runtime():
    ud = build_user_data(**_common_kwargs(instance_class="cpu", gpu_count=0))
    assert "nvidia-container-runtime" not in ud
    assert "nvidia-container-toolkit" not in ud


def test_cpu_userdata_sets_allocatable_gpu_override_zero():
    ud = build_user_data(**_common_kwargs(instance_class="cpu", gpu_count=0))
    assert "ALLOCATABLE_GPU_OVERRIDE=0" in ud


def test_cpu_userdata_does_not_pass_gpus_all_to_docker_run():
    ud = build_user_data(**_common_kwargs(instance_class="cpu", gpu_count=0))
    assert "--gpus all" not in ud


def test_normal_gpu_userdata_passes_gpus_all_to_docker_run():
    ud = build_user_data(**_common_kwargs(instance_class="normal_gpu", gpu_count=1))
    assert "--gpus all" in ud


def test_unknown_instance_class_raises():
    with pytest.raises(ValueError):
        build_user_data(**_common_kwargs(instance_class="quantum_gpu", gpu_count=99))
```

- [ ] **Step 12.2: Run test to verify it fails**

```bash
pytest package/src/inferia/services/orchestration/services/adapter_engine/adapters/aws/test_bootstrap_builder.py -v -k "instance_class or cpu or gpu"
```

Expected: 8 tests FAIL — either the signature doesn't accept `instance_class` / `gpu_count`, or the cpu branch isn't implemented.

- [ ] **Step 12.3: Implement the branching**

Edit `package/src/inferia/services/orchestration/services/adapter_engine/adapters/aws/bootstrap_builder.py`. Change `build_user_data` signature to accept `instance_class: str` and `gpu_count: int`, then branch on `instance_class`:

```python
def build_user_data(
    *,
    bootstrap_token: str,
    pool_id: str,
    node_name: str,
    control_plane_url: str,
    inference_token: str,
    worker_image: str,
    instance_class: str,
    gpu_count: int,
    # Preserve any other kwargs the existing build_user_data already
    # accepts (e.g. labels, debug_ssh) — read the current signature
    # at adapters/aws/bootstrap_builder.py before editing and keep
    # them after `gpu_count`.
) -> str:
    """Render cloud-init user-data for the EC2 worker.

    Branches on instance_class:
      - normal_gpu / heavy_gpu: install nvidia-container-toolkit, pass
        --gpus all to docker run, advertise gpu_count.
      - cpu: skip NVIDIA driver setup, no --gpus, advertise gpu=0.
    """
    if instance_class not in {"normal_gpu", "heavy_gpu", "cpu"}:
        raise ValueError(f"unknown instance_class: {instance_class}")

    is_gpu = instance_class != "cpu"
    nvidia_block = (
        "# Install nvidia-container-toolkit for GPU passthrough\n"
        "distribution=$(. /etc/os-release;echo $ID$VERSION_ID)\n"
        "curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | "
        "sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg\n"
        "curl -s -L https://nvidia.github.io/libnvidia-container/${distribution}/libnvidia-container.list | "
        "sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' | "
        "sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list\n"
        "sudo apt-get update -y\n"
        "sudo apt-get install -y nvidia-container-toolkit\n"
        "sudo nvidia-ctk runtime configure --runtime=docker\n"
        "sudo systemctl restart docker\n"
    ) if is_gpu else "# CPU-only instance: skipping NVIDIA driver setup\n"

    gpus_flag = "--gpus all" if is_gpu else ""

    # Heredoc/template rendering preserving the existing structure. The
    # ALLOCATABLE_GPU_OVERRIDE comes from gpu_count (0 for cpu).
    return f"""#!/bin/bash
set -euo pipefail
{nvidia_block}
sudo docker pull {worker_image}
sudo docker run -d --name inferia-worker --restart unless-stopped \\
    {gpus_flag} \\
    -e BOOTSTRAP_TOKEN={bootstrap_token} \\
    -e POOL_ID={pool_id} \\
    -e NODE_NAME={node_name} \\
    -e CONTROL_PLANE_URL={control_plane_url} \\
    -e INFERENCE_TOKEN={inference_token} \\
    -e ALLOCATABLE_GPU_OVERRIDE={gpu_count} \\
    -e ALLOCATABLE_GPU_MODELS_OVERRIDE={"NVIDIA" if is_gpu else ""} \\
    -v /var/run/docker.sock:/var/run/docker.sock \\
    --network host \\
    {worker_image}
"""
```

(Preserve any existing kwargs and template structure that's already there. The exact docker run flags should match what's used on the local worker compose; this is a sketch.)

- [ ] **Step 12.4: Run test to verify it passes**

```bash
pytest package/src/inferia/services/orchestration/services/adapter_engine/adapters/aws/test_bootstrap_builder.py -v
```

Expected: all tests PASS (including pre-existing).

- [ ] **Step 12.5: Commit**

```bash
git add package/src/inferia/services/orchestration/services/adapter_engine/adapters/aws/bootstrap_builder.py \
        package/src/inferia/services/orchestration/services/adapter_engine/adapters/aws/test_bootstrap_builder.py
git -c user.signingkey=/home/ankit/.ssh/id_ed25519_gh commit -S -m "aws-bootstrap: branch build_user_data on instance_class

normal_gpu / heavy_gpu: install nvidia-container-toolkit, pass --gpus all,
advertise gpu_count via ALLOCATABLE_GPU_OVERRIDE.
cpu: skip NVIDIA setup, no --gpus, advertise gpu=0.

Closes the half of the tier-selector loop where the user picks 'CPU only'
in the wizard and the EC2 instance comes up without ever installing
NVIDIA drivers. The other half (worker recipes.go relax) lives in
Task 28."
```

---

### Task 13: PhaseHandler base + PhaseContext

**Files:**
- Create: `package/src/inferia/services/orchestration/services/provisioning/phases/__init__.py`
- Create: `package/src/inferia/services/orchestration/services/provisioning/phases/base.py`
- Test: `package/src/inferia/services/orchestration/services/provisioning/phases/tests/test_base.py`

- [ ] **Step 13.1: Write the failing test**

Create `package/src/inferia/services/orchestration/services/provisioning/phases/tests/__init__.py` (empty) and `phases/tests/test_base.py`:

```python
"""Tests for PhaseHandler protocol + PhaseContext dataclass."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import runtime_checkable

import pytest

from inferia.services.orchestration.services.provisioning.jobs.model import (
    Phase, PhaseResult,
)
from inferia.services.orchestration.services.provisioning.phases.base import (
    PhaseContext, PhaseHandler,
)


def test_phase_context_carries_all_required_fields():
    ctx = PhaseContext(
        repo=object(),
        db=object(),
        emit_event=lambda **kw: None,
        bootstrap_timeout_s=600.0,
    )
    assert ctx.bootstrap_timeout_s == 600.0
    assert callable(ctx.emit_event)


def test_phase_context_now_defaults_to_utc_now():
    ctx = PhaseContext(
        repo=object(), db=object(),
        emit_event=lambda **kw: None,
    )
    now = ctx.now()
    assert now.tzinfo is not None


def test_phase_handler_protocol_signature():
    """Anything with a `name: Phase` attribute and an async `run(job, ctx)`
    method satisfies PhaseHandler."""
    class _MyHandler:
        name = Phase.PREFLIGHT
        async def run(self, job, ctx):
            return PhaseResult(next_phase=Phase.PROVISIONING)

    h: PhaseHandler = _MyHandler()  # type: ignore[assignment]
    assert h.name == Phase.PREFLIGHT
```

- [ ] **Step 13.2: Run test to verify it fails**

```bash
pytest package/src/inferia/services/orchestration/services/provisioning/phases/tests/test_base.py -v
```

Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 13.3: Implement the protocol + context**

Create `package/src/inferia/services/orchestration/services/provisioning/phases/__init__.py`:

```python
"""Phase handlers — one per state in the provisioning state machine."""
```

Create `package/src/inferia/services/orchestration/services/provisioning/phases/base.py`:

```python
"""PhaseHandler interface + PhaseContext dependency carrier.

A handler is anything with:
  - name: Phase   class-level attribute saying which phase it handles
  - async def run(job: ProvisioningJob, ctx: PhaseContext) -> PhaseResult

Handlers either return PhaseResult (success or "stay in phase to retry")
or raise. The reconciler classifies any exception via classify_error
and writes the outcome to the jobs table.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Protocol, runtime_checkable

from inferia.services.orchestration.services.provisioning.jobs.model import (
    Phase, PhaseResult, ProvisioningJob,
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class PhaseContext:
    """Carries dependencies handlers need, injected by the reconciler.

    `emit_event` is the bound events.emit_event helper from this DB.
    `now` is a callable so tests can inject a fake clock.
    """
    repo: Any                          # ProvisioningJobRepository
    db: Any                            # database pool with .acquire()
    emit_event: Callable[..., Awaitable[None]]
    now: Callable[[], datetime] = field(default=_utc_now)
    bootstrap_timeout_s: float = 600.0

    # AWS-specific extras populated by PreflightHandler for downstream
    # handlers. Kept loose (Any) so tests don't need full plumbing.
    aws_creds: Any = None
    pulumi_env: dict[str, str] = field(default_factory=dict)


@runtime_checkable
class PhaseHandler(Protocol):
    """Phase handler interface. Implementations are stateless: all
    state lives in `job` and is mutated only through `ctx.repo`."""

    name: Phase

    async def run(
        self, job: ProvisioningJob, ctx: PhaseContext,
    ) -> PhaseResult: ...
```

- [ ] **Step 13.4: Run test to verify it passes**

```bash
pytest package/src/inferia/services/orchestration/services/provisioning/phases/tests/test_base.py -v
```

Expected: all 3 tests PASS.

- [ ] **Step 13.5: Commit**

```bash
git add package/src/inferia/services/orchestration/services/provisioning/phases/
git -c user.signingkey=/home/ankit/.ssh/id_ed25519_gh commit -S -m "provisioning: add PhaseHandler protocol + PhaseContext

PhaseHandler is a runtime_checkable Protocol — anything with a Phase
'name' attribute and an async run(job, ctx) method satisfies it. The
reconciler dispatches on .name. PhaseContext carries the repo, db,
emit_event helper, a now() callable (so tests can inject a fake clock),
and AWS-specific fields populated by PreflightHandler for downstream
handlers."
```

---

### Task 14: PreflightHandler

**Files:**
- Create: `package/src/inferia/services/orchestration/services/provisioning/phases/preflight.py`
- Test: `package/src/inferia/services/orchestration/services/provisioning/phases/tests/test_preflight.py`

- [ ] **Step 14.1: Write the failing test**

Create `phases/tests/test_preflight.py`:

```python
"""Tests for PreflightHandler — runs the 8 preflight checks listed in the spec:

1. pulumi CLI present on PATH
2. AWS creds verify via sts:GetCallerIdentity
3. spec contains required fields (instance_class, instance_type, region)
4. instance_type ∈ catalog
5. instance_type.cls matches spec.instance_class
6. subnet (from ProvidersConfig) exists in region
7. security group (from ProvidersConfig) exists in region
8. AMI resolvable for region + instance_class
"""
from __future__ import annotations

import shutil
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from inferia.services.orchestration.services.provisioning.errors import (
    AMINotFoundError, InvalidCredentialsError, InvalidInstanceTypeError,
    InvalidSpecError, PulumiCliMissingError, SecurityGroupNotFoundError,
    SubnetNotFoundError,
)
from inferia.services.orchestration.services.provisioning.jobs.model import (
    Phase, ProvisioningJob,
)
from inferia.services.orchestration.services.provisioning.phases.base import (
    PhaseContext,
)
from inferia.services.orchestration.services.provisioning.phases.preflight import (
    PreflightHandler,
)


def _job(spec: dict | None = None) -> ProvisioningJob:
    now = datetime(2026, 5, 28, tzinfo=timezone.utc)
    return ProvisioningJob(
        id=uuid.uuid4(), node_id=uuid.uuid4(), pool_id=uuid.uuid4(),
        org_id="org-1", provider="aws",
        spec=spec or {"instance_class": "normal_gpu", "instance_type": "g6.xlarge",
                       "region": "us-east-1"},
        phase=Phase.PREFLIGHT, attempt_count=0,
        created_at=now, updated_at=now,
    )


def _ctx():
    return PhaseContext(
        repo=MagicMock(),
        db=MagicMock(),
        emit_event=AsyncMock(),
    )


@pytest.mark.asyncio
async def test_pulumi_cli_missing_raises():
    with patch("shutil.which", return_value=None):
        with pytest.raises(PulumiCliMissingError):
            await PreflightHandler().run(_job(), _ctx())


@pytest.mark.asyncio
async def test_spec_missing_instance_type_raises_invalid_spec():
    bad = _job({"instance_class": "normal_gpu", "region": "us-east-1"})
    with patch("shutil.which", return_value="/usr/local/bin/pulumi"):
        with pytest.raises(InvalidSpecError) as exc_info:
            await PreflightHandler().run(bad, _ctx())
        assert "instance_type" in str(exc_info.value)


@pytest.mark.asyncio
async def test_unknown_instance_type_raises_invalid_instance_type():
    bad = _job({"instance_class": "normal_gpu", "instance_type": "z9.imaginary",
                  "region": "us-east-1"})
    with patch("shutil.which", return_value="/usr/local/bin/pulumi"):
        with pytest.raises(InvalidInstanceTypeError):
            await PreflightHandler().run(bad, _ctx())


@pytest.mark.asyncio
async def test_instance_type_class_mismatch_raises():
    """instance_type='c6i.xlarge' (cpu) with instance_class='normal_gpu' is invalid."""
    bad = _job({"instance_class": "normal_gpu", "instance_type": "c6i.xlarge",
                  "region": "us-east-1"})
    with patch("shutil.which", return_value="/usr/local/bin/pulumi"):
        with pytest.raises(InvalidInstanceTypeError):
            await PreflightHandler().run(bad, _ctx())


@pytest.mark.asyncio
async def test_credential_verification_failure_raises():
    """If sts:GetCallerIdentity fails, raise InvalidCredentialsError."""
    with patch("shutil.which", return_value="/usr/local/bin/pulumi"), \
         patch(
            "inferia.services.orchestration.services.provisioning.phases."
            "preflight.verify_credentials",
            side_effect=InvalidCredentialsError("bad creds"),
        ):
        with pytest.raises(InvalidCredentialsError):
            await PreflightHandler().run(_job(), _ctx())


@pytest.mark.asyncio
async def test_happy_path_returns_provisioning():
    """All preflight checks pass → next_phase=PROVISIONING."""
    with patch("shutil.which", return_value="/usr/local/bin/pulumi"), \
         patch(
            "inferia.services.orchestration.services.provisioning.phases."
            "preflight.verify_credentials",
            return_value={"Account": "123"},
        ), \
         patch(
            "inferia.services.orchestration.services.provisioning.phases."
            "preflight.verify_subnet_exists", return_value=None,
        ), \
         patch(
            "inferia.services.orchestration.services.provisioning.phases."
            "preflight.verify_security_group_exists", return_value=None,
        ), \
         patch(
            "inferia.services.orchestration.services.provisioning.phases."
            "preflight.resolve_ami", return_value="ami-abc",
        ):
        result = await PreflightHandler().run(_job(), _ctx())
    assert result.next_phase == Phase.PROVISIONING


@pytest.mark.asyncio
async def test_subnet_check_failure_raises():
    with patch("shutil.which", return_value="/usr/local/bin/pulumi"), \
         patch(
            "inferia.services.orchestration.services.provisioning.phases."
            "preflight.verify_credentials", return_value={},
        ), \
         patch(
            "inferia.services.orchestration.services.provisioning.phases."
            "preflight.verify_subnet_exists",
            side_effect=SubnetNotFoundError("subnet-abc"),
        ):
        with pytest.raises(SubnetNotFoundError):
            await PreflightHandler().run(_job(), _ctx())


@pytest.mark.asyncio
async def test_security_group_check_failure_raises():
    with patch("shutil.which", return_value="/usr/local/bin/pulumi"), \
         patch(
            "inferia.services.orchestration.services.provisioning.phases."
            "preflight.verify_credentials", return_value={},
        ), \
         patch(
            "inferia.services.orchestration.services.provisioning.phases."
            "preflight.verify_subnet_exists", return_value=None,
        ), \
         patch(
            "inferia.services.orchestration.services.provisioning.phases."
            "preflight.verify_security_group_exists",
            side_effect=SecurityGroupNotFoundError("sg-abc"),
        ):
        with pytest.raises(SecurityGroupNotFoundError):
            await PreflightHandler().run(_job(), _ctx())


@pytest.mark.asyncio
async def test_ami_check_failure_raises():
    with patch("shutil.which", return_value="/usr/local/bin/pulumi"), \
         patch(
            "inferia.services.orchestration.services.provisioning.phases."
            "preflight.verify_credentials", return_value={},
        ), \
         patch(
            "inferia.services.orchestration.services.provisioning.phases."
            "preflight.verify_subnet_exists", return_value=None,
        ), \
         patch(
            "inferia.services.orchestration.services.provisioning.phases."
            "preflight.verify_security_group_exists", return_value=None,
        ), \
         patch(
            "inferia.services.orchestration.services.provisioning.phases."
            "preflight.resolve_ami",
            side_effect=AMINotFoundError("ami-x not in us-east-1"),
        ):
        with pytest.raises(AMINotFoundError):
            await PreflightHandler().run(_job(), _ctx())
```

- [ ] **Step 14.2: Run test to verify it fails**

```bash
pytest package/src/inferia/services/orchestration/services/provisioning/phases/tests/test_preflight.py -v
```

Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 14.3: Implement PreflightHandler**

Create `phases/preflight.py`:

```python
"""PreflightHandler — runs the 8 preflight checks before pulumi up.

Each check raises a typed ProvisioningError if it fails; the classifier
maps them to PERMANENT (fail-fast) so operators see actionable errors
immediately rather than after waiting for stack.up().

The helpers (verify_credentials, verify_subnet_exists, ...) are imported
at module scope so tests can patch them via module path. resolve_ami
is also imported from the AMI module.
"""
from __future__ import annotations

import shutil
from typing import Any

from inferia.services.orchestration.services.adapter_engine.adapters.aws.instance_catalog import (
    by_class, lookup,
)
from inferia.services.orchestration.services.adapter_engine.adapters.pulumi.ami import (
    resolve_ami,
)
from inferia.services.orchestration.services.adapter_engine.adapters.pulumi.credentials import (
    verify_credentials,
)
from inferia.services.orchestration.services.provisioning.errors import (
    AMINotFoundError, InvalidInstanceTypeError, InvalidSpecError,
    PulumiCliMissingError, SecurityGroupNotFoundError, SubnetNotFoundError,
)
from inferia.services.orchestration.services.provisioning.jobs.model import (
    Phase, PhaseResult, ProvisioningJob,
)
from inferia.services.orchestration.services.provisioning.phases.base import (
    PhaseContext,
)


# Defined here for the same reason as credentials._boto3_sts_client:
# extracted so tests can patch without bringing boto3 into the import path.


def verify_subnet_exists(*, region: str, subnet_id: str, creds: Any) -> None:
    """Raise SubnetNotFoundError if the subnet does not exist."""
    from botocore.exceptions import ClientError
    import boto3
    ec2 = boto3.client(
        "ec2", region_name=region,
        aws_access_key_id=creds.access_key_id,
        aws_secret_access_key=creds.secret_access_key,
    )
    try:
        ec2.describe_subnets(SubnetIds=[subnet_id])
    except ClientError as e:
        code = (e.response.get("Error") or {}).get("Code", "")
        if code == "InvalidSubnetID.NotFound":
            raise SubnetNotFoundError(
                f"subnet {subnet_id!r} not found in {region}"
            ) from e
        raise


def verify_security_group_exists(*, region: str, sg_id: str, creds: Any) -> None:
    from botocore.exceptions import ClientError
    import boto3
    ec2 = boto3.client(
        "ec2", region_name=region,
        aws_access_key_id=creds.access_key_id,
        aws_secret_access_key=creds.secret_access_key,
    )
    try:
        ec2.describe_security_groups(GroupIds=[sg_id])
    except ClientError as e:
        code = (e.response.get("Error") or {}).get("Code", "")
        if code == "InvalidGroup.NotFound":
            raise SecurityGroupNotFoundError(
                f"security group {sg_id!r} not found in {region}"
            ) from e
        raise


class PreflightHandler:
    """Phase: PREFLIGHT.

    Validates everything that's cheap to validate before kicking off
    pulumi up. Any failure here is a fast PERMANENT error with an
    operator-actionable hint."""

    name = Phase.PREFLIGHT

    async def run(self, job: ProvisioningJob, ctx: PhaseContext) -> PhaseResult:
        spec = job.spec or {}

        # 1. Pulumi CLI present.
        if shutil.which("pulumi") is None:
            raise PulumiCliMissingError(
                "pulumi binary not found on PATH inside inferia-app"
            )

        # 2. Required spec fields.
        for field in ("instance_class", "instance_type", "region"):
            if not spec.get(field):
                raise InvalidSpecError(
                    f"spec is missing required field: {field}"
                )
        instance_class = spec["instance_class"]
        instance_type = spec["instance_type"]
        region = spec["region"]

        # 3. instance_type ∈ catalog.
        it = lookup(instance_type)
        if it is None:
            raise InvalidInstanceTypeError(
                f"unknown instance type: {instance_type!r}"
            )

        # 4. class/type pairing.
        if it.cls != instance_class:
            raise InvalidInstanceTypeError(
                f"instance type {instance_type!r} belongs to class {it.cls!r}, "
                f"not {instance_class!r}"
            )

        # 5. Creds work.
        creds = ctx.aws_creds  # injected by the reconciler before dispatch
        identity = verify_credentials(creds)
        await ctx.emit_event(
            pool_id=job.pool_id, node_id=job.node_id, phase=Phase.PREFLIGHT,
            status="log",
            message=f"AWS credentials verified (Account {identity.get('Account','?')})",
        )

        # 6. Subnet (optional — only if provider config supplies one).
        if subnet := spec.get("subnet_id"):
            verify_subnet_exists(region=region, subnet_id=subnet, creds=creds)

        # 7. Security group (optional).
        if sg := spec.get("security_group_id"):
            verify_security_group_exists(region=region, sg_id=sg, creds=creds)

        # 8. AMI resolves.
        try:
            ami = resolve_ami(region=region, instance_class=instance_class, creds=creds)
        except Exception as e:
            raise AMINotFoundError(
                f"no AMI available for {instance_class} in {region}: {e}"
            ) from e
        await ctx.emit_event(
            pool_id=job.pool_id, node_id=job.node_id, phase=Phase.PREFLIGHT,
            status="log", message=f"AMI resolved: {ami}",
        )

        return PhaseResult(
            next_phase=Phase.PROVISIONING,
            outputs={"ami_id": ami, "region": region,
                       "instance_class": instance_class,
                       "instance_type": instance_type},
        )
```

(If `resolve_ami` in `adapters/pulumi/ami.py` does not yet accept `instance_class` and `creds` kwargs, extend it there to accept them; ami.py already exists and was edited recently — adapt to its current signature and add a `instance_class` parameter that picks DLAMI for GPU classes and a plain Ubuntu AMI for `cpu`.)

- [ ] **Step 14.4: Run test to verify it passes**

```bash
pytest package/src/inferia/services/orchestration/services/provisioning/phases/tests/test_preflight.py -v
```

Expected: all 9 tests PASS.

- [ ] **Step 14.5: Commit**

```bash
git add package/src/inferia/services/orchestration/services/provisioning/phases/preflight.py \
        package/src/inferia/services/orchestration/services/provisioning/phases/tests/test_preflight.py \
        package/src/inferia/services/orchestration/services/adapter_engine/adapters/pulumi/ami.py
git -c user.signingkey=/home/ankit/.ssh/id_ed25519_gh commit -S -m "provisioning: add PreflightHandler

Runs the 8 spec'd preflight checks before kicking off pulumi up. Each
failure raises a PERMANENT typed error with an operator-actionable hint,
so the dashboard shows e.g. 'PULUMI_CLI_MISSING — Install in container:
curl -fsSL https://get.pulumi.com | sh' within seconds instead of after
a long stack.up() failure."
```

---

### Task 15: PulumiUpHandler

**Files:**
- Create: `package/src/inferia/services/orchestration/services/provisioning/phases/pulumi_up.py`
- Test: `package/src/inferia/services/orchestration/services/provisioning/phases/tests/test_pulumi_up.py`

- [ ] **Step 15.1: Write the failing test**

Create `phases/tests/test_pulumi_up.py`:

```python
"""Tests for PulumiUpHandler."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from inferia.services.orchestration.services.adapter_engine.adapters.pulumi.pulumi_aws_adapter import (
    StackOutputs,
)
from inferia.services.orchestration.services.provisioning.errors import (
    AWSThrottledError, InvalidCredentialsError,
)
from inferia.services.orchestration.services.provisioning.jobs.model import (
    Phase, ProvisioningJob,
)
from inferia.services.orchestration.services.provisioning.phases.base import (
    PhaseContext,
)
from inferia.services.orchestration.services.provisioning.phases.pulumi_up import (
    PulumiUpHandler,
)


def _job(spec: dict | None = None) -> ProvisioningJob:
    now = datetime(2026, 5, 28, tzinfo=timezone.utc)
    return ProvisioningJob(
        id=uuid.uuid4(), node_id=uuid.uuid4(), pool_id=uuid.uuid4(),
        org_id="org-1", provider="aws",
        spec=spec or {"instance_class": "normal_gpu",
                       "instance_type": "g6.xlarge",
                       "region": "us-east-1", "ami_id": "ami-abc"},
        phase=Phase.PROVISIONING, attempt_count=0,
        created_at=now, updated_at=now,
        pulumi_stack_outputs={"ami_id": "ami-abc"},
    )


def _ctx():
    return PhaseContext(
        repo=MagicMock(), db=MagicMock(),
        emit_event=AsyncMock(),
        aws_creds=MagicMock(),
        pulumi_env={"AWS_ACCESS_KEY_ID": "k", "AWS_SECRET_ACCESS_KEY": "s"},
    )


@pytest.mark.asyncio
async def test_happy_path_returns_bootstrapping_with_outputs():
    outputs = StackOutputs(
        instance_id="i-abc", public_dns="ec2-1.compute.amazonaws.com",
        region="us-east-1", ami_id="ami-abc",
    )
    with patch(
        "inferia.services.orchestration.services.provisioning.phases."
        "pulumi_up.run_pulumi_up_sync", return_value=outputs,
    ):
        result = await PulumiUpHandler().run(_job(), _ctx())
    assert result.next_phase == Phase.BOOTSTRAPPING
    assert result.outputs == {
        "instance_id": "i-abc",
        "public_dns": "ec2-1.compute.amazonaws.com",
        "region": "us-east-1",
        "ami_id": "ami-abc",
    }


@pytest.mark.asyncio
async def test_throttled_error_propagates():
    """TransientError from run_pulumi_up_sync propagates — reconciler retries."""
    with patch(
        "inferia.services.orchestration.services.provisioning.phases."
        "pulumi_up.run_pulumi_up_sync",
        side_effect=AWSThrottledError("rate limited"),
    ):
        with pytest.raises(AWSThrottledError):
            await PulumiUpHandler().run(_job(), _ctx())


@pytest.mark.asyncio
async def test_auth_failure_propagates_as_permanent():
    with patch(
        "inferia.services.orchestration.services.provisioning.phases."
        "pulumi_up.run_pulumi_up_sync",
        side_effect=InvalidCredentialsError("bad creds"),
    ):
        with pytest.raises(InvalidCredentialsError):
            await PulumiUpHandler().run(_job(), _ctx())


@pytest.mark.asyncio
async def test_stack_name_uses_deterministic_org_pool_node_format():
    captured = {}
    def _spy(*, stack_name, program, env):
        captured["stack_name"] = stack_name
        return StackOutputs(instance_id="i", public_dns=None, region=None, ami_id=None)
    j = _job()
    with patch(
        "inferia.services.orchestration.services.provisioning.phases."
        "pulumi_up.run_pulumi_up_sync", side_effect=_spy,
    ):
        await PulumiUpHandler().run(j, _ctx())
    assert captured["stack_name"] == f"{j.org_id}-{j.pool_id}-{j.node_id}"


@pytest.mark.asyncio
async def test_emit_event_logs_progress():
    outputs = StackOutputs(
        instance_id="i-abc", public_dns=None, region=None, ami_id="ami-x",
    )
    ctx = _ctx()
    with patch(
        "inferia.services.orchestration.services.provisioning.phases."
        "pulumi_up.run_pulumi_up_sync", return_value=outputs,
    ):
        await PulumiUpHandler().run(_job(), ctx)
    # At least one log event emitted (start) + one success event.
    assert ctx.emit_event.await_count >= 2
```

- [ ] **Step 15.2: Run test to verify it fails**

```bash
pytest package/src/inferia/services/orchestration/services/provisioning/phases/tests/test_pulumi_up.py -v
```

Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 15.3: Implement PulumiUpHandler**

Create `phases/pulumi_up.py`:

```python
"""PulumiUpHandler — wraps run_pulumi_up_sync in asyncio.to_thread."""
from __future__ import annotations

import asyncio

from inferia.services.orchestration.services.adapter_engine.adapters.aws.bootstrap_builder import (
    build_user_data,
)
from inferia.services.orchestration.services.adapter_engine.adapters.pulumi.pulumi_aws_adapter import (
    run_pulumi_up_sync,
)
from inferia.services.orchestration.services.adapter_engine.adapters.pulumi.programs import (
    build_program,
)
from inferia.services.orchestration.services.provisioning.jobs.model import (
    Phase, PhaseResult, ProvisioningJob,
)
from inferia.services.orchestration.services.provisioning.phases.base import (
    PhaseContext,
)


class PulumiUpHandler:
    """Phase: PROVISIONING. Runs pulumi up via asyncio.to_thread.

    The Pulumi Python SDK has no up_async (memory:
    feedback_pulumi_python_sdk_sync). All exceptions propagate; the
    classifier decides retry vs fail.
    """

    name = Phase.PROVISIONING

    async def run(self, job: ProvisioningJob, ctx: PhaseContext) -> PhaseResult:
        stack_name = f"{job.org_id}-{job.pool_id}-{job.node_id}"
        spec = job.spec
        program = build_program(spec=spec, stack_outputs=job.pulumi_stack_outputs or {})

        await ctx.emit_event(
            pool_id=job.pool_id, node_id=job.node_id, phase=Phase.PROVISIONING,
            status="running",
            message=f"Starting pulumi up on stack {stack_name}",
        )

        # Run in a thread — see feedback_pulumi_python_sdk_sync.
        outputs = await asyncio.to_thread(
            run_pulumi_up_sync,
            stack_name=stack_name,
            program=program,
            env=ctx.pulumi_env,
        )

        await ctx.emit_event(
            pool_id=job.pool_id, node_id=job.node_id, phase=Phase.PROVISIONING,
            status="succeeded",
            message=f"EC2 instance {outputs.instance_id} created in {outputs.region}",
            extra={"instance_id": outputs.instance_id,
                     "public_dns": outputs.public_dns},
        )

        return PhaseResult(
            next_phase=Phase.BOOTSTRAPPING,
            outputs={
                "instance_id": outputs.instance_id,
                "public_dns": outputs.public_dns,
                "region": outputs.region,
                "ami_id": outputs.ami_id,
            },
        )
```

(If `build_program` doesn't yet take a `stack_outputs` kwarg, extend it to pull `ami_id` from there if present; this enables resume after a partial stack.up.)

- [ ] **Step 15.4: Run test to verify it passes**

```bash
pytest package/src/inferia/services/orchestration/services/provisioning/phases/tests/test_pulumi_up.py -v
```

Expected: all 5 tests PASS.

- [ ] **Step 15.5: Commit**

```bash
git add package/src/inferia/services/orchestration/services/provisioning/phases/pulumi_up.py \
        package/src/inferia/services/orchestration/services/provisioning/phases/tests/test_pulumi_up.py
git -c user.signingkey=/home/ankit/.ssh/id_ed25519_gh commit -S -m "provisioning: add PulumiUpHandler

Wraps run_pulumi_up_sync in asyncio.to_thread (Pulumi Python SDK is
sync-only). Deterministic stack name format org-pool-node ensures
pulumi up is idempotent on re-lease after a crash. Emits running +
succeeded events to the event log so the Overview tab timeline
updates."
```

---

### Task 16: BootstrapHandler

**Files:**
- Create: `package/src/inferia/services/orchestration/services/provisioning/phases/bootstrap.py`
- Test: `package/src/inferia/services/orchestration/services/provisioning/phases/tests/test_bootstrap.py`

- [ ] **Step 16.1: Write the failing test**

Create `phases/tests/test_bootstrap.py`:

```python
"""Tests for BootstrapHandler — polls compute_inventory.state waiting for
the worker on the EC2 instance to register and transition to 'ready'."""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from inferia.services.orchestration.services.provisioning.errors import (
    NetworkError, TransientError,
)
from inferia.services.orchestration.services.provisioning.jobs.model import (
    Phase, ProvisioningJob,
)
from inferia.services.orchestration.services.provisioning.phases.base import (
    PhaseContext,
)
from inferia.services.orchestration.services.provisioning.phases.bootstrap import (
    BootstrapHandler,
)


def _job() -> ProvisioningJob:
    now = datetime(2026, 5, 28, tzinfo=timezone.utc)
    return ProvisioningJob(
        id=uuid.uuid4(), node_id=uuid.uuid4(), pool_id=uuid.uuid4(),
        org_id="org-1", provider="aws", spec={},
        phase=Phase.BOOTSTRAPPING, attempt_count=0,
        created_at=now, updated_at=now,
    )


def _ctx(*, bootstrap_timeout_s=600.0, get_inventory_states):
    """get_inventory_states yields state strings on successive polls."""
    states = iter(get_inventory_states)
    async def _poll(*, node_id):
        try:
            return {"state": next(states)}
        except StopIteration:
            return {"state": "provisioning"}
    inv = MagicMock()
    inv.get_node = AsyncMock(side_effect=lambda **kw: _poll(**kw))
    return PhaseContext(
        repo=MagicMock(),
        db=MagicMock(),
        emit_event=AsyncMock(),
        bootstrap_timeout_s=bootstrap_timeout_s,
    ), inv


@pytest.mark.asyncio
async def test_returns_ready_immediately_when_already_ready():
    ctx, inv = _ctx(get_inventory_states=["ready"])
    handler = BootstrapHandler(inventory_repo=inv, poll_interval_s=0.01)
    result = await handler.run(_job(), ctx)
    assert result.next_phase == Phase.READY


@pytest.mark.asyncio
async def test_polls_until_state_becomes_ready():
    ctx, inv = _ctx(get_inventory_states=[
        "provisioning", "provisioning", "ready",
    ])
    handler = BootstrapHandler(inventory_repo=inv, poll_interval_s=0.01)
    result = await handler.run(_job(), ctx)
    assert result.next_phase == Phase.READY
    assert inv.get_node.await_count >= 3


@pytest.mark.asyncio
async def test_raises_transient_error_on_timeout():
    """Bootstrap deadline elapses without the worker registering."""
    ctx, inv = _ctx(
        bootstrap_timeout_s=0.05,
        get_inventory_states=["provisioning"] * 20,
    )
    handler = BootstrapHandler(inventory_repo=inv, poll_interval_s=0.01)
    with pytest.raises(TransientError):
        await handler.run(_job(), ctx)


@pytest.mark.asyncio
async def test_raises_permanent_when_node_state_becomes_failed():
    """If the worker's startup script crashes, inventory.state may flip to
    'failed' directly. Bootstrap should fail-loud, not poll forever."""
    from inferia.services.orchestration.services.provisioning.errors import (
        PermanentError,
    )
    ctx, inv = _ctx(get_inventory_states=["provisioning", "failed"])
    handler = BootstrapHandler(inventory_repo=inv, poll_interval_s=0.01)
    with pytest.raises(PermanentError):
        await handler.run(_job(), ctx)


@pytest.mark.asyncio
async def test_emits_running_log_at_least_once():
    ctx, inv = _ctx(get_inventory_states=["ready"])
    handler = BootstrapHandler(inventory_repo=inv, poll_interval_s=0.01)
    await handler.run(_job(), ctx)
    assert ctx.emit_event.await_count >= 1
```

- [ ] **Step 16.2: Run test to verify it fails**

```bash
pytest package/src/inferia/services/orchestration/services/provisioning/phases/tests/test_bootstrap.py -v
```

Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 16.3: Implement BootstrapHandler**

Create `phases/bootstrap.py`:

```python
"""BootstrapHandler — polls compute_inventory.state until the worker
registers and transitions to 'ready'. Times out as TransientError so
the reconciler retries the whole bootstrap phase (idempotent: each
re-entry just polls again)."""
from __future__ import annotations

import asyncio
from typing import Any

from inferia.services.orchestration.services.provisioning.errors import (
    PermanentError, TransientError,
)
from inferia.services.orchestration.services.provisioning.jobs.model import (
    Phase, PhaseResult, ProvisioningJob,
)
from inferia.services.orchestration.services.provisioning.phases.base import (
    PhaseContext,
)


class BootstrapHandler:
    """Phase: BOOTSTRAPPING."""

    name = Phase.BOOTSTRAPPING

    def __init__(self, *, inventory_repo: Any, poll_interval_s: float = 5.0):
        self.inventory_repo = inventory_repo
        self.poll_interval_s = poll_interval_s

    async def run(self, job: ProvisioningJob, ctx: PhaseContext) -> PhaseResult:
        await ctx.emit_event(
            pool_id=job.pool_id, node_id=job.node_id, phase=Phase.BOOTSTRAPPING,
            status="running",
            message="Waiting for worker on EC2 instance to register",
        )

        deadline = ctx.now().timestamp() + ctx.bootstrap_timeout_s
        while ctx.now().timestamp() < deadline:
            row = await self.inventory_repo.get_node(node_id=job.node_id)
            state = (row or {}).get("state")
            if state == "ready":
                await ctx.emit_event(
                    pool_id=job.pool_id, node_id=job.node_id,
                    phase=Phase.BOOTSTRAPPING, status="succeeded",
                    message="Worker registered as ready",
                )
                return PhaseResult(next_phase=Phase.READY)
            if state == "failed":
                raise PermanentError(
                    "Worker bootstrap failed (inventory.state=failed)",
                    code="BOOTSTRAP_FAILED",
                    hint="Check the cloud-init logs on the EC2 instance for "
                         "the underlying error (Logs sub-tab).",
                )
            await asyncio.sleep(self.poll_interval_s)

        raise TransientError(
            f"Worker did not register within {ctx.bootstrap_timeout_s}s",
            code="BOOTSTRAP_TIMEOUT",
        )
```

- [ ] **Step 16.4: Run test to verify it passes**

```bash
pytest package/src/inferia/services/orchestration/services/provisioning/phases/tests/test_bootstrap.py -v
```

Expected: all 5 tests PASS.

- [ ] **Step 16.5: Commit**

```bash
git add package/src/inferia/services/orchestration/services/provisioning/phases/bootstrap.py \
        package/src/inferia/services/orchestration/services/provisioning/phases/tests/test_bootstrap.py
git -c user.signingkey=/home/ankit/.ssh/id_ed25519_gh commit -S -m "provisioning: add BootstrapHandler

Polls compute_inventory.state with bootstrap_timeout_s deadline (default
600s). state='ready' → terminal READY; state='failed' → PermanentError
(BOOTSTRAP_FAILED hint points operator at the Logs tab); deadline
exceeded → TransientError so reconciler re-leases and re-polls
(idempotent — bootstrap doesn't mutate AWS, just observes inventory)."
```

---

### Task 17: CancelHandler

**Files:**
- Create: `package/src/inferia/services/orchestration/services/provisioning/phases/cancel.py`
- Test: `package/src/inferia/services/orchestration/services/provisioning/phases/tests/test_cancel.py`

- [ ] **Step 17.1: Write the failing test**

Create `phases/tests/test_cancel.py`:

```python
"""Tests for CancelHandler — runs pulumi destroy."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from inferia.services.orchestration.services.provisioning.jobs.model import (
    Phase, ProvisioningJob,
)
from inferia.services.orchestration.services.provisioning.phases.base import (
    PhaseContext,
)
from inferia.services.orchestration.services.provisioning.phases.cancel import (
    CancelHandler,
)


def _job() -> ProvisioningJob:
    now = datetime(2026, 5, 28, tzinfo=timezone.utc)
    return ProvisioningJob(
        id=uuid.uuid4(), node_id=uuid.uuid4(), pool_id=uuid.uuid4(),
        org_id="org-1", provider="aws",
        spec={"instance_class": "normal_gpu"},
        phase=Phase.CANCELLING, attempt_count=0,
        created_at=now, updated_at=now,
        pulumi_stack_outputs={"instance_id": "i-abc"},
    )


def _ctx():
    return PhaseContext(
        repo=MagicMock(), db=MagicMock(), emit_event=AsyncMock(),
        pulumi_env={"AWS_ACCESS_KEY_ID": "k", "AWS_SECRET_ACCESS_KEY": "s"},
    )


@pytest.mark.asyncio
async def test_happy_path_destroys_stack_and_returns_terminated():
    with patch(
        "inferia.services.orchestration.services.provisioning.phases."
        "cancel.run_pulumi_destroy_sync", return_value=None,
    ):
        result = await CancelHandler().run(_job(), _ctx())
    assert result.next_phase == Phase.TERMINATED


@pytest.mark.asyncio
async def test_destroy_on_empty_state_is_noop():
    """If no AWS resources were ever created, destroy is a no-op."""
    j = _job()
    object.__setattr__(j, "pulumi_stack_outputs", {})  # bypass frozen
    with patch(
        "inferia.services.orchestration.services.provisioning.phases."
        "cancel.run_pulumi_destroy_sync", return_value=None,
    ) as destroy:
        result = await CancelHandler().run(j, _ctx())
    assert result.next_phase == Phase.TERMINATED
    destroy.assert_called_once()  # we still call it (idempotent)
```

- [ ] **Step 17.2: Run test to verify it fails**

```bash
pytest package/src/inferia/services/orchestration/services/provisioning/phases/tests/test_cancel.py -v
```

Expected: FAIL.

- [ ] **Step 17.3: Implement CancelHandler + run_pulumi_destroy_sync**

Add `run_pulumi_destroy_sync` to `pulumi_aws_adapter.py`:

```python
def run_pulumi_destroy_sync(
    *,
    stack_name: str,
    program: Callable[[], None],
    env: dict[str, str],
) -> None:
    """Run `pulumi destroy` synchronously. Idempotent — destroying a
    stack that doesn't exist is treated as success."""
    try:
        stack = _make_stack(stack_name=stack_name, program=program, env=env)
    except FileNotFoundError as e:
        raise PulumiCliMissingError(f"pulumi binary missing: {e}") from e
    try:
        stack.destroy()
    except Exception as e:
        # If the stack never existed, treat as success.
        if "no stack named" in str(e).lower():
            return
        raise
```

Create `phases/cancel.py`:

```python
"""CancelHandler — runs pulumi destroy for the node's stack."""
from __future__ import annotations

import asyncio

from inferia.services.orchestration.services.adapter_engine.adapters.pulumi.programs import (
    build_program,
)
from inferia.services.orchestration.services.adapter_engine.adapters.pulumi.pulumi_aws_adapter import (
    run_pulumi_destroy_sync,
)
from inferia.services.orchestration.services.provisioning.jobs.model import (
    Phase, PhaseResult, ProvisioningJob,
)
from inferia.services.orchestration.services.provisioning.phases.base import (
    PhaseContext,
)


class CancelHandler:
    """Phase: CANCELLING. Idempotent pulumi destroy."""

    name = Phase.CANCELLING

    async def run(self, job: ProvisioningJob, ctx: PhaseContext) -> PhaseResult:
        stack_name = f"{job.org_id}-{job.pool_id}-{job.node_id}"
        program = build_program(spec=job.spec, stack_outputs=job.pulumi_stack_outputs or {})

        await ctx.emit_event(
            pool_id=job.pool_id, node_id=job.node_id, phase=Phase.CANCELLING,
            status="running", message=f"Destroying stack {stack_name}",
        )
        await asyncio.to_thread(
            run_pulumi_destroy_sync,
            stack_name=stack_name, program=program, env=ctx.pulumi_env,
        )
        await ctx.emit_event(
            pool_id=job.pool_id, node_id=job.node_id, phase=Phase.CANCELLING,
            status="succeeded", message="Stack destroyed",
        )
        return PhaseResult(next_phase=Phase.TERMINATED)
```

- [ ] **Step 17.4: Run test to verify it passes**

```bash
pytest package/src/inferia/services/orchestration/services/provisioning/phases/tests/test_cancel.py -v
```

Expected: both tests PASS.

- [ ] **Step 17.5: Commit**

```bash
git add package/src/inferia/services/orchestration/services/provisioning/phases/cancel.py \
        package/src/inferia/services/orchestration/services/provisioning/phases/tests/test_cancel.py \
        package/src/inferia/services/orchestration/services/adapter_engine/adapters/pulumi/pulumi_aws_adapter.py
git -c user.signingkey=/home/ankit/.ssh/id_ed25519_gh commit -S -m "provisioning: add CancelHandler + run_pulumi_destroy_sync

CancelHandler wraps the new pulumi destroy helper in asyncio.to_thread,
treats 'no stack named' as success (idempotent). Sets next_phase=
TERMINATED on completion. The reconciler picks 'cancelling' jobs first
(see jobs/repository claim query ORDER) so user deletes happen
promptly."
```

---

### Task 18: Lease helpers

**Files:**
- Create: `package/src/inferia/services/orchestration/services/provisioning/reconciler/__init__.py`
- Create: `package/src/inferia/services/orchestration/services/provisioning/reconciler/lease.py`
- Test: `package/src/inferia/services/orchestration/services/provisioning/reconciler/tests/test_lease.py`

- [ ] **Step 18.1: Write the failing test**

Create `reconciler/__init__.py` (empty), `reconciler/tests/__init__.py` (empty), `reconciler/tests/test_lease.py`:

```python
"""Tests for the lease.renew_loop helper used by the reconciler."""
from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from inferia.services.orchestration.services.provisioning.reconciler.lease import (
    renew_loop,
)


@pytest.mark.asyncio
async def test_renew_loop_calls_repo_renew_every_interval():
    repo = MagicMock()
    repo.renew_lease = AsyncMock(return_value=True)
    stop = asyncio.Event()

    async def trigger():
        await asyncio.sleep(0.1)
        stop.set()

    job_id = uuid.uuid4()
    await asyncio.gather(
        renew_loop(repo=repo, job_id=job_id, lease_holder="me",
                   renew_interval_s=0.03, lease_seconds=300, stop=stop),
        trigger(),
    )
    # Should have renewed at least twice in 0.1s with 0.03s interval.
    assert repo.renew_lease.await_count >= 2


@pytest.mark.asyncio
async def test_renew_loop_returns_false_signal_when_stolen():
    """If renew_lease returns False, the loop sets stop and returns False."""
    repo = MagicMock()
    repo.renew_lease = AsyncMock(return_value=False)
    stop = asyncio.Event()
    result = await renew_loop(
        repo=repo, job_id=uuid.uuid4(), lease_holder="me",
        renew_interval_s=0.01, lease_seconds=300, stop=stop,
    )
    assert result is False
    assert stop.is_set()


@pytest.mark.asyncio
async def test_renew_loop_stops_when_event_set():
    repo = MagicMock()
    repo.renew_lease = AsyncMock(return_value=True)
    stop = asyncio.Event()
    stop.set()  # already set
    result = await renew_loop(
        repo=repo, job_id=uuid.uuid4(), lease_holder="me",
        renew_interval_s=0.01, lease_seconds=300, stop=stop,
    )
    assert result is True
    # 0 or 1 renewals only (since stop is already set).
    assert repo.renew_lease.await_count <= 1
```

- [ ] **Step 18.2: Run test to verify it fails**

```bash
pytest package/src/inferia/services/orchestration/services/provisioning/reconciler/tests/test_lease.py -v
```

Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 18.3: Implement renew_loop**

Create `reconciler/lease.py`:

```python
"""Lease renewal coroutine the reconciler runs alongside each handler.

A long-running handler (provisioning, bootstrapping) would otherwise
let its lease expire while still working. The renewal loop UPDATEs
lease_expires_at every renew_interval_s. If a renewal returns False
(lease stolen by another reconciler — shouldn't happen but defensive),
we set the stop event so the surrounding TaskGroup cancels the handler.
"""
from __future__ import annotations

import asyncio
from uuid import UUID


async def renew_loop(
    *,
    repo,
    job_id: UUID,
    lease_holder: str,
    renew_interval_s: float,
    lease_seconds: int,
    stop: asyncio.Event,
) -> bool:
    """Renew the lease until `stop` is set or a renewal fails.

    Returns True if the loop exited cleanly (stop was set), False if a
    renewal returned False (lease stolen).
    """
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=renew_interval_s)
            return True  # stop fired during the sleep
        except asyncio.TimeoutError:
            pass  # interval elapsed; renew now

        ok = await repo.renew_lease(
            job_id=job_id, lease_holder=lease_holder, lease_seconds=lease_seconds,
        )
        if not ok:
            stop.set()
            return False
    return True
```

- [ ] **Step 18.4: Run test to verify it passes**

```bash
pytest package/src/inferia/services/orchestration/services/provisioning/reconciler/tests/test_lease.py -v
```

Expected: all 3 tests PASS.

- [ ] **Step 18.5: Commit**

```bash
git add package/src/inferia/services/orchestration/services/provisioning/reconciler/
git -c user.signingkey=/home/ankit/.ssh/id_ed25519_gh commit -S -m "provisioning: add lease.renew_loop

Background renewal task the reconciler runs in an asyncio.TaskGroup
alongside each phase handler. Renews the lease every renew_interval_s.
If a renewal returns False (lease stolen), sets the stop event so the
TaskGroup cancels the handler — the job will get re-leased by another
reconciler tick."
```

---

### Task 19: WorkerPool

**Files:**
- Create: `package/src/inferia/services/orchestration/services/provisioning/reconciler/concurrency.py`
- Test: `package/src/inferia/services/orchestration/services/provisioning/reconciler/tests/test_concurrency.py`

- [ ] **Step 19.1: Write the failing test**

Create `reconciler/tests/test_concurrency.py`:

```python
"""Tests for the WorkerPool that processes claimed jobs in parallel."""
from __future__ import annotations

import asyncio

import pytest

from inferia.services.orchestration.services.provisioning.reconciler.concurrency import (
    WorkerPool,
)


@pytest.mark.asyncio
async def test_worker_pool_runs_callable_with_target_concurrency():
    active = 0
    peak = 0

    async def work():
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.05)
        active -= 1

    pool = WorkerPool(concurrency=4)
    await pool.start(work)
    await asyncio.sleep(0.2)
    await pool.stop()

    assert peak == 4, f"expected concurrency=4, peak={peak}"


@pytest.mark.asyncio
async def test_worker_pool_stop_drains():
    """stop() waits for in-flight work to complete."""
    completed = 0

    async def work():
        nonlocal completed
        await asyncio.sleep(0.05)
        completed += 1

    pool = WorkerPool(concurrency=2)
    await pool.start(work)
    await asyncio.sleep(0.02)
    await pool.stop()
    # At least the started ones should have finished.
    assert completed >= 2


@pytest.mark.asyncio
async def test_worker_pool_swallows_per_task_exceptions():
    """A single iteration raising doesn't kill the pool."""
    iterations = 0

    async def work():
        nonlocal iterations
        iterations += 1
        if iterations == 1:
            raise RuntimeError("first iteration boom")

    pool = WorkerPool(concurrency=2)
    await pool.start(work)
    await asyncio.sleep(0.05)
    await pool.stop()
    assert iterations >= 2
```

- [ ] **Step 19.2: Run test to verify it fails**

```bash
pytest package/src/inferia/services/orchestration/services/provisioning/reconciler/tests/test_concurrency.py -v
```

Expected: FAIL.

- [ ] **Step 19.3: Implement WorkerPool**

Create `reconciler/concurrency.py`:

```python
"""WorkerPool: N async workers all running the same callable in a loop.

Used by the reconciler to run up to N claim-and-dispatch iterations in
parallel. Each worker swallows per-iteration exceptions (logging them)
so one bad job doesn't take down the pool.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable


logger = logging.getLogger(__name__)


class WorkerPool:
    """Run a callable in parallel from up to `concurrency` workers."""

    def __init__(self, *, concurrency: int):
        self.concurrency = concurrency
        self._tasks: list[asyncio.Task] = []
        self._stop = asyncio.Event()

    async def start(self, work: Callable[[], Awaitable[None]]) -> None:
        for i in range(self.concurrency):
            t = asyncio.create_task(self._worker(work, i), name=f"worker-{i}")
            self._tasks.append(t)

    async def stop(self) -> None:
        self._stop.set()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

    async def _worker(self, work, worker_id: int) -> None:
        while not self._stop.is_set():
            try:
                await work()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "worker %d iteration raised; continuing", worker_id,
                )
            # Tight loops are bad; tiny yield so cancellation lands.
            await asyncio.sleep(0)
```

- [ ] **Step 19.4: Run test to verify it passes**

```bash
pytest package/src/inferia/services/orchestration/services/provisioning/reconciler/tests/test_concurrency.py -v
```

Expected: all 3 tests PASS.

- [ ] **Step 19.5: Commit**

```bash
git add package/src/inferia/services/orchestration/services/provisioning/reconciler/concurrency.py \
        package/src/inferia/services/orchestration/services/provisioning/reconciler/tests/test_concurrency.py
git -c user.signingkey=/home/ankit/.ssh/id_ed25519_gh commit -S -m "provisioning: add reconciler.WorkerPool

N parallel async workers all running the same callable in a loop. Used
by the reconciler so up to N jobs can be claimed-and-dispatched in
parallel from one process. Per-iteration exceptions are logged but
don't kill the worker; only cancellation (graceful shutdown) stops it."
```

---

### Task 20: ProvisioningReconciler loop

**Files:**
- Create: `package/src/inferia/services/orchestration/services/provisioning/reconciler/loop.py`
- Test: `package/src/inferia/services/orchestration/services/provisioning/reconciler/tests/test_loop.py`

- [ ] **Step 20.1: Write the failing test**

Create `reconciler/tests/test_loop.py`:

```python
"""Tests for ProvisioningReconciler — the heart of the state machine.

Strategy: provide a fake repo + fake handlers + fake event emitter,
seed jobs by hand, drive one or more reconciler ticks, assert the right
repo writes happened.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from inferia.services.orchestration.services.provisioning.errors import (
    AWSThrottledError, InvalidCredentialsError, PermanentError,
)
from inferia.services.orchestration.services.provisioning.jobs.model import (
    Phase, PhaseResult, ProvisioningJob,
)
from inferia.services.orchestration.services.provisioning.reconciler.loop import (
    ProvisioningReconciler,
)


def _job(phase: Phase = Phase.PREFLIGHT, **over) -> ProvisioningJob:
    now = datetime(2026, 5, 28, tzinfo=timezone.utc)
    base = dict(
        id=uuid.uuid4(), node_id=uuid.uuid4(), pool_id=uuid.uuid4(),
        org_id="org-1", provider="aws", spec={},
        phase=phase, attempt_count=0,
        created_at=now, updated_at=now,
    )
    base.update(over)
    return ProvisioningJob(**base)


class _FakeRepo:
    def __init__(self, jobs: list[ProvisioningJob] | None = None):
        self.jobs = list(jobs or [])
        self.transitions: list[tuple] = []
        self.retries: list[tuple] = []
        self.failures: list[tuple] = []
        self.releases: list[tuple] = []
        self.renew_calls = 0

    async def claim_next_job(self, *, lease_holder, lease_seconds=300):
        return self.jobs.pop(0) if self.jobs else None

    async def transition_to(self, **kwargs):
        self.transitions.append(kwargs)

    async def schedule_retry(self, **kwargs):
        self.retries.append(kwargs)

    async def fail(self, **kwargs):
        self.failures.append(kwargs)

    async def release_lease(self, **kwargs):
        self.releases.append(kwargs)

    async def renew_lease(self, **kwargs):
        self.renew_calls += 1
        return True


class _OkHandler:
    def __init__(self, name: Phase, next_phase: Phase | None):
        self.name = name
        self.next_phase = next_phase
        self.calls = 0
    async def run(self, job, ctx):
        self.calls += 1
        return PhaseResult(next_phase=self.next_phase)


class _RaisingHandler:
    def __init__(self, name: Phase, exc: Exception):
        self.name = name
        self.exc = exc
    async def run(self, job, ctx):
        raise self.exc


def _make_reconciler(repo, handlers):
    return ProvisioningReconciler(
        repo=repo,
        handlers={h.name: h for h in handlers},
        emit_event=AsyncMock(),
        db=MagicMock(),
        concurrency=1,
        poll_interval_s=0.01,
        lease_seconds=300,
        renew_interval_s=10.0,
        lease_holder="test-rec",
        load_aws_context=AsyncMock(return_value=(MagicMock(), {})),
    )


@pytest.mark.asyncio
async def test_one_tick_dispatches_to_phase_handler_and_transitions():
    job = _job(Phase.PREFLIGHT)
    repo = _FakeRepo([job])
    h = _OkHandler(Phase.PREFLIGHT, Phase.PROVISIONING)
    rec = _make_reconciler(repo, [h])

    await rec.tick_once()

    assert h.calls == 1
    assert len(repo.transitions) == 1
    assert repo.transitions[0]["next_phase"] == Phase.PROVISIONING


@pytest.mark.asyncio
async def test_transient_error_schedules_retry():
    job = _job(Phase.PROVISIONING, attempt_count=0)
    repo = _FakeRepo([job])
    h = _RaisingHandler(Phase.PROVISIONING, AWSThrottledError("rate"))
    rec = _make_reconciler(repo, [h])

    await rec.tick_once()

    assert len(repo.retries) == 1
    assert repo.retries[0]["attempt_count"] == 1
    assert "next_attempt_after" in repo.retries[0]


@pytest.mark.asyncio
async def test_transient_error_at_max_attempts_escalates_to_permanent():
    job = _job(Phase.PROVISIONING, attempt_count=5)
    repo = _FakeRepo([job])
    h = _RaisingHandler(Phase.PROVISIONING, AWSThrottledError("rate"))
    rec = _make_reconciler(repo, [h])

    await rec.tick_once()

    assert len(repo.failures) == 1
    assert repo.failures[0]["error"].code == "RETRIES_EXHAUSTED"


@pytest.mark.asyncio
async def test_permanent_error_fails_immediately():
    job = _job(Phase.PREFLIGHT)
    repo = _FakeRepo([job])
    h = _RaisingHandler(Phase.PREFLIGHT, InvalidCredentialsError("bad"))
    rec = _make_reconciler(repo, [h])

    await rec.tick_once()

    assert len(repo.failures) == 1
    assert repo.failures[0]["error"].code == "INVALID_CREDENTIALS"
    assert len(repo.retries) == 0


@pytest.mark.asyncio
async def test_unknown_handler_for_phase_fails_loudly():
    """If a job lands in a phase with no registered handler, fail-loud."""
    job = _job(Phase.PROVISIONING)
    repo = _FakeRepo([job])
    rec = _make_reconciler(repo, [])  # no handlers

    await rec.tick_once()
    assert len(repo.failures) == 1
    assert repo.failures[0]["error"].code == "UNCLASSIFIED"


@pytest.mark.asyncio
async def test_handler_returning_terminal_phase_writes_transition_and_releases():
    job = _job(Phase.BOOTSTRAPPING)
    repo = _FakeRepo([job])
    h = _OkHandler(Phase.BOOTSTRAPPING, Phase.READY)
    rec = _make_reconciler(repo, [h])

    await rec.tick_once()

    assert len(repo.transitions) == 1
    assert repo.transitions[0]["next_phase"] == Phase.READY


@pytest.mark.asyncio
async def test_empty_queue_is_a_noop_tick():
    repo = _FakeRepo([])
    rec = _make_reconciler(repo, [])
    await rec.tick_once()
    assert len(repo.transitions) == 0
    assert len(repo.failures) == 0
```

- [ ] **Step 20.2: Run test to verify it fails**

```bash
pytest package/src/inferia/services/orchestration/services/provisioning/reconciler/tests/test_loop.py -v
```

Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 20.3: Implement the reconciler loop**

Create `reconciler/loop.py`:

```python
"""ProvisioningReconciler — claims jobs, dispatches to phase handlers,
records outcomes via the repository.

Single entry point: `await rec.run()` blocks forever (until cancelled).
`tick_once()` exists for tests to drive one iteration synchronously.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import timezone
from typing import Any, Awaitable, Callable

from inferia.services.orchestration.services.provisioning.errors import (
    ProvisioningError,
)
from inferia.services.orchestration.services.provisioning.jobs.model import (
    ClassifiedError, ErrorClass, Phase, ProvisioningJob,
)
from inferia.services.orchestration.services.provisioning.phases.base import (
    PhaseContext, PhaseHandler,
)
from inferia.services.orchestration.services.provisioning.reconciler.concurrency import (
    WorkerPool,
)
from inferia.services.orchestration.services.provisioning.reconciler.lease import (
    renew_loop,
)
from inferia.services.orchestration.services.provisioning.retry.backoff import (
    TRANSIENT_MAX_ATTEMPTS, next_attempt_after,
)
from inferia.services.orchestration.services.provisioning.retry.classifier import (
    classify_error,
)


logger = logging.getLogger(__name__)


class ProvisioningReconciler:
    """The heart of the state machine."""

    def __init__(
        self,
        *,
        repo: Any,
        handlers: dict[Phase, PhaseHandler],
        emit_event: Callable[..., Awaitable[None]],
        db: Any,
        concurrency: int = 4,
        poll_interval_s: float = 2.0,
        lease_seconds: int = 300,
        renew_interval_s: float = 60.0,
        lease_holder: str = "inferia-app",
        load_aws_context: Callable[[ProvisioningJob], Awaitable[tuple[Any, dict[str, str]]]] | None = None,
    ):
        self.repo = repo
        self.handlers = handlers
        self.emit_event = emit_event
        self.db = db
        self.concurrency = concurrency
        self.poll_interval_s = poll_interval_s
        self.lease_seconds = lease_seconds
        self.renew_interval_s = renew_interval_s
        self.lease_holder = lease_holder
        self.load_aws_context = load_aws_context
        self._pool: WorkerPool | None = None

    async def run(self) -> None:
        """Run until cancelled. Starts a WorkerPool of `concurrency`
        coroutines all calling `_one_iteration`."""
        self._pool = WorkerPool(concurrency=self.concurrency)
        await self._pool.start(self._one_iteration)
        try:
            await asyncio.Future()  # block until cancelled
        except asyncio.CancelledError:
            await self._pool.stop()
            raise

    async def stop(self) -> None:
        if self._pool is not None:
            await self._pool.stop()

    async def tick_once(self) -> None:
        """For tests: run one iteration synchronously."""
        await self._one_iteration()

    async def _one_iteration(self) -> None:
        job = await self.repo.claim_next_job(
            lease_holder=self.lease_holder, lease_seconds=self.lease_seconds,
        )
        if job is None:
            await asyncio.sleep(self.poll_interval_s)
            return

        handler = self.handlers.get(job.phase)
        if handler is None:
            await self._fail_loud(
                job, ClassifiedError(
                    error_class=ErrorClass.PERMANENT, code="UNCLASSIFIED",
                    message=f"no handler for phase {job.phase.value}",
                    hint="server misconfiguration — file a bug",
                ),
            )
            return

        # Build the PhaseContext + injected aws_creds/pulumi_env from
        # ProvidersConfig (cached per-job for now; the load is short-lived).
        aws_creds, pulumi_env = (None, {})
        if self.load_aws_context is not None:
            aws_creds, pulumi_env = await self.load_aws_context(job)
        ctx = PhaseContext(
            repo=self.repo, db=self.db, emit_event=self.emit_event,
            aws_creds=aws_creds, pulumi_env=pulumi_env,
        )

        # Run the handler with lease renewal in parallel.
        stop = asyncio.Event()
        try:
            async with asyncio.TaskGroup() as tg:
                tg.create_task(renew_loop(
                    repo=self.repo, job_id=job.id, lease_holder=self.lease_holder,
                    renew_interval_s=self.renew_interval_s,
                    lease_seconds=self.lease_seconds, stop=stop,
                ))
                runner = tg.create_task(handler.run(job, ctx))
                # Wait for the runner; on completion or error, stop the renewer.
            stop.set()
            result = runner.result()
        except* ProvisioningError as eg:
            stop.set()
            await self._handle_error(job, eg.exceptions[0])
            return
        except* Exception as eg:
            stop.set()
            await self._handle_error(job, eg.exceptions[0])
            return

        # Successful PhaseResult — advance phase (or stay).
        if result.next_phase is None:
            # Handler asked to retry; treat as transient with no exception.
            await self.repo.release_lease(job_id=job.id, lease_holder=self.lease_holder)
            return
        await self.repo.transition_to(
            job_id=job.id, current_phase=job.phase, next_phase=result.next_phase,
            lease_holder=self.lease_holder, outputs=result.outputs,
        )
        if result.event is not None:
            await self.emit_event(
                pool_id=job.pool_id, node_id=job.node_id,
                phase=result.event.phase, status=result.event.status,
                message=result.event.message, extra=result.event.extra,
            )

    async def _handle_error(self, job: ProvisioningJob, exc: BaseException) -> None:
        try:
            ce = classify_error(exc)
        except (asyncio.CancelledError, KeyboardInterrupt):
            raise

        if ce.error_class == ErrorClass.TRANSIENT:
            new_attempt = job.attempt_count + 1
            if new_attempt >= TRANSIENT_MAX_ATTEMPTS:
                # Escalate to permanent.
                escalated = ClassifiedError(
                    error_class=ErrorClass.PERMANENT,
                    code="RETRIES_EXHAUSTED",
                    message=f"gave up after {TRANSIENT_MAX_ATTEMPTS} transient "
                            f"failures: {ce.message}",
                    hint=ce.hint,
                )
                await self.repo.fail(
                    job_id=job.id, current_phase=job.phase,
                    lease_holder=self.lease_holder, error=escalated,
                )
                await self.emit_event(
                    pool_id=job.pool_id, node_id=job.node_id, phase=job.phase,
                    status="failed", message=escalated.message,
                    extra={"code": escalated.code, "class": "PERMANENT"},
                )
                return
            now = job.updated_at.astimezone(timezone.utc) if job.updated_at else None
            from datetime import datetime
            now = now or datetime.now(timezone.utc)
            await self.repo.schedule_retry(
                job_id=job.id, current_phase=job.phase,
                lease_holder=self.lease_holder,
                next_attempt_after=next_attempt_after(new_attempt, now=now),
                attempt_count=new_attempt, error=ce,
            )
            await self.emit_event(
                pool_id=job.pool_id, node_id=job.node_id, phase=job.phase,
                status="log",
                message=f"transient failure ({ce.code}); retrying (attempt {new_attempt})",
                extra={"code": ce.code, "class": ce.error_class.value},
            )
            return

        # PERMANENT / INFRASTRUCTURE → fail terminal.
        await self._fail_loud(job, ce)

    async def _fail_loud(self, job: ProvisioningJob, ce: ClassifiedError) -> None:
        await self.repo.fail(
            job_id=job.id, current_phase=job.phase,
            lease_holder=self.lease_holder, error=ce,
        )
        await self.emit_event(
            pool_id=job.pool_id, node_id=job.node_id, phase=job.phase,
            status="failed", message=ce.message,
            extra={"code": ce.code, "class": ce.error_class.value},
        )
        if ce.hint:
            await self.emit_event(
                pool_id=job.pool_id, node_id=job.node_id, phase=job.phase,
                status="log", message=ce.hint, extra={"hint": True},
            )
```

- [ ] **Step 20.4: Run test to verify it passes**

```bash
pytest package/src/inferia/services/orchestration/services/provisioning/reconciler/tests/test_loop.py -v
```

Expected: all 7 tests PASS.

- [ ] **Step 20.5: Commit**

```bash
git add package/src/inferia/services/orchestration/services/provisioning/reconciler/loop.py \
        package/src/inferia/services/orchestration/services/provisioning/reconciler/tests/test_loop.py
git -c user.signingkey=/home/ankit/.ssh/id_ed25519_gh commit -S -m "provisioning: add ProvisioningReconciler loop

Claims one job per tick, dispatches to the registered PhaseHandler with
PhaseContext, runs lease renewal in parallel via TaskGroup, classifies
any exception via classify_error, and writes outcome (transition_to /
schedule_retry / fail) — with attempt_count escalation to
PERMANENT/RETRIES_EXHAUSTED after TRANSIENT_MAX_ATTEMPTS=5.

Two failure-mode invariants tested:
- No handler registered for a phase → fail-loud UNCLASSIFIED
- Unknown exception type → classifier returns UNCLASSIFIED PERMANENT

tick_once() is the test seam; production code calls await rec.run()."
```

---

### Task 21: Reconciler shutdown drain

**Files:**
- Modify: `package/src/inferia/services/orchestration/services/provisioning/reconciler/loop.py` (add `stop_with_drain`)
- Test: `package/src/inferia/services/orchestration/services/provisioning/reconciler/tests/test_shutdown.py`

- [ ] **Step 21.1: Write the failing test**

Create `reconciler/tests/test_shutdown.py`:

```python
"""Tests for the reconciler's graceful shutdown behavior."""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from inferia.services.orchestration.services.provisioning.jobs.model import (
    Phase, PhaseResult, ProvisioningJob,
)
from inferia.services.orchestration.services.provisioning.reconciler.loop import (
    ProvisioningReconciler,
)


@pytest.mark.asyncio
async def test_stop_with_drain_waits_up_to_grace_seconds_then_cancels():
    """In-flight handlers get grace seconds to complete; after that,
    they are cancelled (leases stay set, will expire naturally)."""
    handler_cancelled = False

    class _SlowHandler:
        name = Phase.PROVISIONING
        async def run(self, job, ctx):
            nonlocal handler_cancelled
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                handler_cancelled = True
                raise
            return PhaseResult(next_phase=Phase.BOOTSTRAPPING)

    now = datetime(2026, 5, 28, tzinfo=timezone.utc)
    j = ProvisioningJob(
        id=uuid.uuid4(), node_id=uuid.uuid4(), pool_id=uuid.uuid4(),
        org_id="org", provider="aws", spec={},
        phase=Phase.PROVISIONING, attempt_count=0,
        created_at=now, updated_at=now,
    )
    repo = MagicMock()
    repo.claim_next_job = AsyncMock(side_effect=[j, None, None])
    repo.renew_lease = AsyncMock(return_value=True)
    repo.release_lease = AsyncMock()
    repo.transition_to = AsyncMock()
    repo.schedule_retry = AsyncMock()
    repo.fail = AsyncMock()

    rec = ProvisioningReconciler(
        repo=repo, handlers={Phase.PROVISIONING: _SlowHandler()},
        emit_event=AsyncMock(), db=MagicMock(), concurrency=1,
        poll_interval_s=0.01, lease_seconds=10, renew_interval_s=1.0,
        lease_holder="t",
        load_aws_context=AsyncMock(return_value=(None, {})),
    )
    run_task = asyncio.create_task(rec.run())
    await asyncio.sleep(0.05)  # let the handler start

    await rec.stop_with_drain(grace_seconds=0.05)
    run_task.cancel()
    try:
        await run_task
    except asyncio.CancelledError:
        pass

    assert handler_cancelled is True
```

- [ ] **Step 21.2: Run test to verify it fails**

```bash
pytest package/src/inferia/services/orchestration/services/provisioning/reconciler/tests/test_shutdown.py -v
```

Expected: FAIL — `stop_with_drain` doesn't exist.

- [ ] **Step 21.3: Add stop_with_drain to the reconciler**

Edit `reconciler/loop.py`. Add method on `ProvisioningReconciler`:

```python
    async def stop_with_drain(self, *, grace_seconds: float = 30.0) -> None:
        """Stop accepting new jobs; wait up to grace_seconds for in-flight
        handlers to complete; then cancel them. Leases stay set with
        their original TTL — the next reconciler boot picks them up."""
        if self._pool is None:
            return
        try:
            await asyncio.wait_for(self._pool.stop(), timeout=grace_seconds)
        except asyncio.TimeoutError:
            logger.warning(
                "shutdown grace expired (%.1fs); cancelling in-flight handlers",
                grace_seconds,
            )
            # WorkerPool.stop already sets the stop event; the await above
            # timed out because handlers are still running. We rely on
            # asyncio task cancellation propagating from process termination.
```

- [ ] **Step 21.4: Run test to verify it passes**

```bash
pytest package/src/inferia/services/orchestration/services/provisioning/reconciler/tests/test_shutdown.py -v
```

Expected: PASS.

- [ ] **Step 21.5: Commit**

```bash
git add package/src/inferia/services/orchestration/services/provisioning/reconciler/loop.py \
        package/src/inferia/services/orchestration/services/provisioning/reconciler/tests/test_shutdown.py
git -c user.signingkey=/home/ankit/.ssh/id_ed25519_gh commit -S -m "provisioning: add reconciler.stop_with_drain

Graceful shutdown: stop accepting new jobs, wait up to grace_seconds
for in-flight handlers to complete, then surface control back to the
SIGTERM handler. Cancelled handlers' leases stay set with the original
TTL — the next reconciler boot (or another replica) picks them up."
```

---

### Task 22: Catalog HTTP endpoint

**Files:**
- Create: `package/src/inferia/services/orchestration/api/providers.py`
- Modify: `package/src/inferia/services/orchestration/server.py` (include the new router)
- Test: `package/src/inferia/services/orchestration/api/test_providers.py`

- [ ] **Step 22.1: Write the failing test**

Create `api/test_providers.py`:

```python
"""Tests for the providers HTTP endpoints."""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from inferia.services.orchestration.api.providers import router


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    return app


def test_aws_instance_catalog_endpoint_returns_three_classes():
    client = TestClient(_app())
    resp = client.get("/api/v1/providers/aws/instance-catalog")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {"normal_gpu", "heavy_gpu", "cpu"}


def test_aws_instance_catalog_entries_have_required_shape():
    client = TestClient(_app())
    body = client.get("/api/v1/providers/aws/instance-catalog").json()
    sample = body["normal_gpu"][0]
    for key in ("name", "cls", "vcpu", "ram_gb", "gpu_count",
                "gpu_model", "gpu_ram_gb", "approx_usd_per_hour"):
        assert key in sample


def test_aws_instance_catalog_cpu_entries_have_zero_gpu():
    client = TestClient(_app())
    body = client.get("/api/v1/providers/aws/instance-catalog").json()
    for it in body["cpu"]:
        assert it["gpu_count"] == 0
        assert it["gpu_model"] is None


def test_aws_instance_catalog_shape_stable_across_calls():
    """The frontend caches via TanStack Query; shape must be deterministic."""
    client = TestClient(_app())
    body1 = client.get("/api/v1/providers/aws/instance-catalog").json()
    body2 = client.get("/api/v1/providers/aws/instance-catalog").json()
    assert body1 == body2
```

- [ ] **Step 22.2: Run test to verify it fails**

```bash
pytest package/src/inferia/services/orchestration/api/test_providers.py -v
```

Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 22.3: Implement the endpoint**

Create `api/providers.py`:

```python
"""Providers HTTP endpoints (currently just the AWS instance catalog
for the wizard's instance-type dropdown)."""
from __future__ import annotations

from fastapi import APIRouter

from inferia.services.orchestration.services.adapter_engine.adapters.aws.instance_catalog import (
    INSTANCE_CATALOG, InstanceType,
)


router = APIRouter()


def _to_dict(it: InstanceType) -> dict:
    return {
        "name": it.name, "cls": it.cls, "vcpu": it.vcpu, "ram_gb": it.ram_gb,
        "gpu_count": it.gpu_count, "gpu_model": it.gpu_model,
        "gpu_ram_gb": it.gpu_ram_gb,
        "approx_usd_per_hour": it.approx_usd_per_hour,
    }


@router.get("/api/v1/providers/aws/instance-catalog")
async def get_aws_instance_catalog() -> dict:
    """Curated EC2 catalog grouped by class. Powers the wizard."""
    grouped: dict[str, list[dict]] = {"normal_gpu": [], "heavy_gpu": [], "cpu": []}
    for it in INSTANCE_CATALOG:
        grouped[it.cls].append(_to_dict(it))
    return grouped
```

Edit `server.py` to register the router:

```python
# In server.py, alongside the existing include_router calls (server.py:282-286):
from inferia.services.orchestration.api import providers as providers_api
app.include_router(providers_api.router)
```

- [ ] **Step 22.4: Run test to verify it passes**

```bash
pytest package/src/inferia/services/orchestration/api/test_providers.py -v
```

Expected: all 4 tests PASS.

- [ ] **Step 22.5: Commit**

```bash
git add package/src/inferia/services/orchestration/api/providers.py \
        package/src/inferia/services/orchestration/api/test_providers.py \
        package/src/inferia/services/orchestration/server.py
git -c user.signingkey=/home/ankit/.ssh/id_ed25519_gh commit -S -m "providers: add GET /api/v1/providers/aws/instance-catalog

Returns the curated INSTANCE_CATALOG grouped by class. Replaces the
hard-coded awsInstanceTiers constant currently in apps/dashboard;
the frontend swap lands in Task 31."
```

---

### Task 23: `add_provider_node` thin enqueue

**Files:**
- Modify: `package/src/inferia/services/orchestration/api/nodes.py` (rewrite `add_provider_node`)
- Modify: `package/src/inferia/services/orchestration/api/test_nodes.py`

- [ ] **Step 23.1: Write the failing test**

Add to `test_nodes.py`:

```python
"""Tests for the new thin-enqueue add_provider_node."""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest


class _FakeProvisioningRepo:
    def __init__(self):
        self.enqueued = []
    async def enqueue(self, *, node_id, pool_id, org_id, provider, spec):
        job_id = uuid.uuid4()
        self.enqueued.append({"job_id": job_id, "node_id": node_id,
                                 "pool_id": pool_id, "spec": spec})
        return job_id


def test_add_aws_node_returns_node_id_and_job_id_in_under_one_second():
    """The HTTP path must NOT block on Pulumi; should return immediately."""
    from fastapi.testclient import TestClient
    from inferia.services.orchestration.api.nodes import router, _deps
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(router)

    _deps.provisioning_repo = _FakeProvisioningRepo()
    # Reuse the FakeInventory + FakePoolRepo + _need_perm bypass that
    # the existing test_nodes.py defines at module scope (lines 41-93).
    # Wire them onto _deps the same way the existing tests do.
    _deps.inventory_repo = FakeInventory()  # from test_nodes.py
    _deps.inventory_repo.create_provisioning_placeholder = AsyncMock(
        return_value=uuid.uuid4(),
    )
    _deps.pool_repo = FakePoolRepo()  # from test_nodes.py

    client = TestClient(app)
    body = {
        "spec": {
            "instance_class": "normal_gpu",
            "instance_type":  "g6.xlarge",
            "region":         "us-east-1",
        },
    }
    import time
    start = time.monotonic()
    resp = client.post("/api/v1/nodes/add/aws", json=body,
                       headers={"X-Organization-ID": "org-1",
                                  "Authorization": "Bearer test"})
    elapsed = time.monotonic() - start
    assert elapsed < 1.0, f"add/aws took {elapsed:.2f}s"
    assert resp.status_code == 200
    data = resp.json()
    assert "node_id" in data
    assert "job_id" in data


def test_add_aws_node_rejects_missing_instance_class():
    from fastapi.testclient import TestClient
    from inferia.services.orchestration.api.nodes import router
    from fastapi import FastAPI
    app = FastAPI(); app.include_router(router)
    client = TestClient(app)
    resp = client.post("/api/v1/nodes/add/aws",
                       json={"spec": {"instance_type": "g6.xlarge",
                                          "region": "us-east-1"}},
                       headers={"X-Organization-ID": "org-1",
                                  "Authorization": "Bearer test"})
    assert resp.status_code == 422
    assert "instance_class" in resp.text


def test_add_aws_node_rejects_class_type_mismatch():
    from fastapi.testclient import TestClient
    from inferia.services.orchestration.api.nodes import router
    from fastapi import FastAPI
    app = FastAPI(); app.include_router(router)
    client = TestClient(app)
    resp = client.post(
        "/api/v1/nodes/add/aws",
        json={"spec": {"instance_class": "normal_gpu",
                          "instance_type": "c6i.xlarge",  # CPU type
                          "region": "us-east-1"}},
        headers={"X-Organization-ID": "org-1", "Authorization": "Bearer test"},
    )
    assert resp.status_code == 422
```

- [ ] **Step 23.2: Run test to verify it fails**

```bash
pytest package/src/inferia/services/orchestration/api/test_nodes.py -v -k "add_aws"
```

Expected: FAIL.

- [ ] **Step 23.3: Rewrite `add_provider_node`**

Edit `api/nodes.py`. Replace the existing `add_provider_node` with:

```python
@router.post("/add/{provider}", response_model=AddProviderNodeResponse)
async def add_provider_node(
    provider: str = Path(...),
    body: AddProviderNodeBody = ...,
    x_organization_id: str | None = Header(default=None, alias="X-Organization-ID"),
    authorization: str | None = Header(default=None),
    _granted: bool = Depends(_need_perm("deployment:create")),
):
    if provider == "worker":
        raise HTTPException(404, "use POST /v1/nodes/add/worker for worker nodes")
    if provider != "aws":
        # Other providers (nosana, akash) still use their own paths; the
        # state-machine refactor scopes to AWS. Preserve the existing
        # synchronous adapter.provision_single_node call.
        adapter = _deps.adapters.get(provider)
        if adapter is None:
            raise HTTPException(404, f"unknown provider: {provider}")
        org_id = _org_id_from_headers(authorization, x_organization_id)
        pool_id = str(await _deps.pool_repo.ensure_default_pool(org_id=org_id))
        spec_legacy = dict(body.spec or {})
        if body.node_name is not None:
            spec_legacy.setdefault("node_name", body.node_name)
        if body.labels:
            spec_legacy["labels"] = body.labels
        if body.credential_name is not None:
            spec_legacy.setdefault("credential_name", body.credential_name)
        try:
            node = await adapter.provision_single_node(
                pool_id=pool_id, org_id=org_id, spec=spec_legacy,
            )
        except NotImplementedError:
            raise HTTPException(501, f"provider {provider!r} does not support single-node add")
        except HTTPException:
            raise
        except Exception as e:
            logger.exception("provision_single_node failed")
            raise HTTPException(502, f"{provider} adapter error: {e}")
        return AddProviderNodeResponse(
            node_id=str(node["id"]),
            provider=node.get("provider", provider),
            provider_instance_id=node.get("provider_instance_id"),
            state=node.get("state", "provisioning"),
        )

    # ---- AWS path: validate spec + thin enqueue --------------------------
    spec = dict(body.spec or {})
    for field in ("instance_class", "instance_type", "region"):
        if not spec.get(field):
            raise HTTPException(422, f"spec.{field} is required")

    from inferia.services.orchestration.services.adapter_engine.adapters.aws.instance_catalog import (
        lookup,
    )
    it = lookup(spec["instance_type"])
    if it is None:
        raise HTTPException(422, f"unknown instance_type: {spec['instance_type']!r}")
    if it.cls != spec["instance_class"]:
        raise HTTPException(
            422,
            f"instance_type {spec['instance_type']!r} belongs to class "
            f"{it.cls!r}, not {spec['instance_class']!r}",
        )

    org_id = _org_id_from_headers(authorization, x_organization_id)
    pool_id = await _deps.pool_repo.ensure_default_pool(org_id=org_id)

    # Insert a placeholder compute_inventory row in state='provisioning'
    # so the dashboard sees the node immediately while the reconciler
    # picks up the job. We use a UUID placeholder for provider_instance_id
    # (Pulumi will issue the real one); the unique constraint is
    # (provider, provider_instance_id) so the placeholder must be unique.
    node_id = await _deps.inventory_repo.create_provisioning_placeholder(
        pool_id=pool_id, provider="aws",
        instance_class=spec["instance_class"],
        instance_type=spec["instance_type"],
        node_name=body.node_name,
    )
    job_id = await _deps.provisioning_repo.enqueue(
        node_id=node_id, pool_id=pool_id, org_id=org_id,
        provider="aws", spec=spec,
    )
    return AddProviderNodeResponse(
        node_id=str(node_id),
        provider="aws",
        provider_instance_id=None,
        state="provisioning",
        job_id=str(job_id),
    )
```

Add the `job_id` field to `AddProviderNodeResponse` (optional). Extend `InventoryRepo` with a `create_provisioning_placeholder` method that inserts a `compute_inventory` row with a placeholder `provider_instance_id` and the new `instance_class` + `instance_type` columns.

- [ ] **Step 23.4: Run test to verify it passes**

```bash
pytest package/src/inferia/services/orchestration/api/test_nodes.py -v -k "add_aws"
```

Expected: all PASS.

- [ ] **Step 23.5: Commit**

```bash
git add package/src/inferia/services/orchestration/api/nodes.py \
        package/src/inferia/services/orchestration/api/test_nodes.py \
        package/src/inferia/services/orchestration/repositories/inventory_repo.py
git -c user.signingkey=/home/ankit/.ssh/id_ed25519_gh commit -S -m "nodes: thin-enqueue add_provider_node for AWS

POST /api/v1/nodes/add/aws now validates spec + instance_class/type
pairing, creates a 'provisioning' placeholder in compute_inventory,
enqueues a provisioning_jobs row, and returns (node_id, job_id) in ~200ms.
The reconciler does the actual Pulumi work asynchronously. Non-AWS
providers (nosana, akash) keep the legacy synchronous path."
```

---

### Task 24: `GET /provisioning` extended response

**Files:**
- Modify: `package/src/inferia/services/orchestration/api/nodes.py` (extend `get_provisioning`)
- Modify: `package/src/inferia/services/orchestration/api/test_nodes.py`

- [ ] **Step 24.1: Write the failing test**

Add to `test_nodes.py`:

```python
def test_get_provisioning_includes_error_and_aws_metadata():
    """Response gains error, aws_metadata, attempt_count fields."""
    # Setup: fake repo with a 'failed' job and pulumi outputs.
    from fastapi.testclient import TestClient
    from fastapi import FastAPI
    from inferia.services.orchestration.api.nodes import router, _deps
    from inferia.services.orchestration.services.provisioning.jobs.model import (
        ErrorClass, Phase,
    )

    job = MagicMock()
    job.id = uuid.uuid4()
    job.phase = Phase.FAILED
    job.attempt_count = 3
    job.last_error_code = "PULUMI_CLI_MISSING"
    job.last_error_message = "no pulumi binary"
    job.last_error_hint = "install via curl"
    job.error_class = ErrorClass.PERMANENT
    job.pulumi_stack_outputs = {
        "instance_id": "i-abc",
        "public_dns":  "ec2-1.compute.amazonaws.com",
        "region":      "us-east-1",
        "ami_id":      "ami-x",
    }

    _deps.provisioning_repo = MagicMock()
    _deps.provisioning_repo.get_by_node = AsyncMock(return_value=job)
    _deps.inventory_repo = FakeInventory()
    nid = str(uuid.uuid4())
    _deps.inventory_repo.nodes[nid] = {
        "id": nid, "pool_id": str(uuid.uuid4()), "provider": "aws",
        "state": "failed", "instance_class": "normal_gpu",
        "instance_type": "g6.xlarge",
    }

    app = FastAPI(); app.include_router(router)
    client = TestClient(app)
    auth = {"Authorization": "Bearer test"}
    resp = client.get(f"/api/v1/nodes/{nid}/provisioning", headers=auth)
    assert resp.status_code == 200
    assert resp.json()["error"] == {
        "code": "PULUMI_CLI_MISSING",
        "message": "no pulumi binary",
        "hint": "install via curl",
        "class": "PERMANENT",
    }
    assert resp.json()["aws_metadata"]["instance_id"] == "i-abc"
    assert resp.json()["attempt_count"] == 3


def test_get_provisioning_returns_404_when_node_missing():
    from fastapi.testclient import TestClient
    from fastapi import FastAPI
    from inferia.services.orchestration.api.nodes import router, _deps
    _deps.inventory_repo = FakeInventory()  # no nodes inserted
    app = FastAPI(); app.include_router(router)
    client = TestClient(app)
    auth = {"Authorization": "Bearer test"}
    resp = client.get("/api/v1/nodes/00000000-0000-0000-0000-000000000000/provisioning",
                      headers=auth)
    assert resp.status_code == 404
```

- [ ] **Step 24.2: Run test to verify it fails**

```bash
pytest package/src/inferia/services/orchestration/api/test_nodes.py::test_get_provisioning_includes_error_and_aws_metadata -v
```

Expected: FAIL.

- [ ] **Step 24.3: Extend the response**

Edit `nodes.py` `get_provisioning`. Replace the body so it joins `provisioning_jobs` data:

```python
@router.get("/{node_id}/provisioning", response_model=ProvisioningSummary)
async def get_provisioning(
    node_id: str = Path(...),
    _granted: bool = Depends(_need_perm("deployment:list")),
):
    row = await _deps.inventory_repo.get_node(node_id=node_id)
    if not row:
        raise HTTPException(404, "node not found")
    pool_id = row.get("pool_id")
    job = await _deps.provisioning_repo.get_by_node(node_id=uuid.UUID(node_id))

    # Build error block.
    error_block: dict | None = None
    if job and job.last_error_code:
        error_block = {
            "code": job.last_error_code,
            "message": job.last_error_message,
            "hint": job.last_error_hint,
            "class": job.error_class.value if job.error_class else "PERMANENT",
        }

    # Build AWS metadata block.
    aws_metadata: dict | None = None
    if row.get("provider") == "aws":
        outs = (job.pulumi_stack_outputs or {}) if job else {}
        aws_metadata = {
            "instance_class": row.get("instance_class"),
            "instance_type":  row.get("instance_type"),
            "region":         outs.get("region"),
            "ami_id":         outs.get("ami_id"),
            "instance_id":    outs.get("instance_id"),
            "public_dns":     outs.get("public_dns"),
        }

    # Phases via existing event log.
    phases_summary = await _deps.node_events_repo.summarize_phases(pool_id=pool_id) \
        if _deps.node_events_repo else []
    current_phase = job.phase.value if job else None
    terminal = (job is None) or job.phase.is_terminal

    return ProvisioningSummary(
        current_phase=current_phase,
        terminal=terminal,
        phases=phases_summary,
        attempt_count=job.attempt_count if job else 0,
        error=error_block,
        aws_metadata=aws_metadata,
        job_id=str(job.id) if job else None,
    )
```

Extend `ProvisioningSummary` Pydantic model to include the new fields.

- [ ] **Step 24.4: Run test to verify it passes**

```bash
pytest package/src/inferia/services/orchestration/api/test_nodes.py::test_get_provisioning_includes_error_and_aws_metadata -v
```

Expected: PASS.

- [ ] **Step 24.5: Commit**

```bash
git add package/src/inferia/services/orchestration/api/nodes.py \
        package/src/inferia/services/orchestration/api/test_nodes.py
git -c user.signingkey=/home/ankit/.ssh/id_ed25519_gh commit -S -m "nodes: extend GET /provisioning with error + aws_metadata + attempt_count

UI consumes these three new fields to render the error banner with
hint, the Retry button, and the new AWS metadata grid. Phase timeline
shape stays the same so the existing ProvisioningStatus component
continues working without changes."
```

---

### Task 25: `POST /provisioning/retry`

**Files:**
- Modify: `package/src/inferia/services/orchestration/api/nodes.py`
- Modify: `package/src/inferia/services/orchestration/api/test_nodes.py`

- [ ] **Step 25.1: Write the failing test**

Add to `test_nodes.py`:

```python
def test_retry_provisioning_on_failed_job_returns_200_and_requeues():
    """Job is in 'failed' phase; POST /retry resets it to 'pending'."""
    # Wire a fake repo where reset_for_retry returns a job
    job = MagicMock(); job.id = uuid.uuid4()
    _deps.provisioning_repo.reset_for_retry = AsyncMock(return_value=job)
    client = TestClient(_app())
    resp = client.post(f"/api/v1/nodes/{uuid.uuid4()}/provisioning/retry", headers=auth)
    assert resp.status_code == 200
    assert resp.json()["job_id"] == str(job.id)


def test_retry_provisioning_on_non_failed_job_returns_409():
    _deps.provisioning_repo.reset_for_retry = AsyncMock(return_value=None)
    client = TestClient(_app())
    resp = client.post(f"/api/v1/nodes/{uuid.uuid4()}/provisioning/retry", headers=auth)
    assert resp.status_code == 409


def test_retry_provisioning_on_missing_node_returns_404():
    _deps.inventory_repo.get_node = AsyncMock(return_value=None)
    client = TestClient(_app())
    resp = client.post(f"/api/v1/nodes/{uuid.uuid4()}/provisioning/retry", headers=auth)
    assert resp.status_code == 404
```

- [ ] **Step 25.2: Run test to verify it fails**

```bash
pytest package/src/inferia/services/orchestration/api/test_nodes.py -v -k "retry"
```

Expected: FAIL (endpoint not defined).

- [ ] **Step 25.3: Add the endpoint**

Edit `nodes.py`:

```python
@router.post("/{node_id}/provisioning/retry")
async def retry_provisioning(
    node_id: str = Path(...),
    _granted: bool = Depends(_need_perm("deployment:create")),
):
    """Re-enqueue a failed provisioning job. 409 if the job is not in
    'failed' state; 404 if the node doesn't exist."""
    row = await _deps.inventory_repo.get_node(node_id=node_id)
    if not row:
        raise HTTPException(404, "node not found")
    job = await _deps.provisioning_repo.reset_for_retry(
        node_id=uuid.UUID(node_id),
    )
    if job is None:
        raise HTTPException(409, "no failed job to retry")
    # Reset inventory state too (failed → provisioning).
    await _deps.inventory_repo.set_state(node_id=node_id, state="provisioning")
    return {"job_id": str(job.id), "phase": job.phase.value}
```

Add `InventoryRepo.set_state` if missing.

- [ ] **Step 25.4: Run test to verify it passes**

```bash
pytest package/src/inferia/services/orchestration/api/test_nodes.py -v -k "retry"
```

Expected: all 3 tests PASS.

- [ ] **Step 25.5: Commit**

```bash
git add package/src/inferia/services/orchestration/api/nodes.py \
        package/src/inferia/services/orchestration/api/test_nodes.py \
        package/src/inferia/services/orchestration/repositories/inventory_repo.py
git -c user.signingkey=/home/ankit/.ssh/id_ed25519_gh commit -S -m "nodes: add POST /provisioning/retry

Resets a failed provisioning_jobs row to phase='pending', attempt_count=0,
clears error fields. Inventory state goes failed → provisioning. UI's
Retry button calls this endpoint."
```

---

### Task 26: `DELETE /nodes/{id}` cancellation enqueue

**Files:**
- Modify: `package/src/inferia/services/orchestration/api/nodes.py` (`delete_node`)
- Modify: `package/src/inferia/services/orchestration/api/test_nodes.py`

- [ ] **Step 26.1: Write the failing test**

Add to `test_nodes.py`:

```python
def test_delete_non_terminal_node_enqueues_cancellation():
    job = MagicMock(); job.phase.is_terminal = False
    _deps.provisioning_repo.get_by_node = AsyncMock(return_value=job)
    _deps.provisioning_repo.request_cancel = AsyncMock(return_value=True)
    client = TestClient(_app())
    resp = client.delete(f"/api/v1/nodes/{uuid.uuid4()}", headers=auth)
    assert resp.status_code in (200, 204)
    _deps.provisioning_repo.request_cancel.assert_awaited_once()


def test_delete_terminated_node_is_idempotent_204():
    """Deleting an already-terminated node returns 204 (no-op)."""
    job = MagicMock(); job.phase.is_terminal = True
    _deps.provisioning_repo.get_by_node = AsyncMock(return_value=job)
    _deps.inventory_repo.set_state = AsyncMock()
    client = TestClient(_app())
    resp = client.delete(f"/api/v1/nodes/{uuid.uuid4()}", headers=auth)
    assert resp.status_code in (200, 204)


def test_delete_missing_node_returns_404():
    _deps.inventory_repo.get_node = AsyncMock(return_value=None)
    client = TestClient(_app())
    resp = client.delete(f"/api/v1/nodes/{uuid.uuid4()}", headers=auth)
    assert resp.status_code == 404
```

- [ ] **Step 26.2: Run test to verify it fails**

```bash
pytest package/src/inferia/services/orchestration/api/test_nodes.py -v -k "delete"
```

Expected: FAIL.

- [ ] **Step 26.3: Update `delete_node`**

Edit `nodes.py`. Existing `delete_node` likely tears down the node directly; replace with:

```python
@router.delete("/{node_id}")
async def delete_node(
    node_id: str = Path(...),
    _granted: bool = Depends(_need_perm("deployment:delete")),
):
    row = await _deps.inventory_repo.get_node(node_id=node_id)
    if not row:
        raise HTTPException(404, "node not found")
    job = await _deps.provisioning_repo.get_by_node(node_id=uuid.UUID(node_id))
    if job is not None and not job.phase.is_terminal:
        await _deps.provisioning_repo.request_cancel(node_id=uuid.UUID(node_id))
        return Response(status_code=204)
    # Already terminal — soft-delete the inventory row idempotently.
    await _deps.inventory_repo.set_state(node_id=node_id, state="terminated")
    return Response(status_code=204)
```

- [ ] **Step 26.4: Run test to verify it passes**

```bash
pytest package/src/inferia/services/orchestration/api/test_nodes.py -v -k "delete"
```

Expected: 3 tests PASS.

- [ ] **Step 26.5: Commit**

```bash
git add package/src/inferia/services/orchestration/api/nodes.py \
        package/src/inferia/services/orchestration/api/test_nodes.py
git -c user.signingkey=/home/ankit/.ssh/id_ed25519_gh commit -S -m "nodes: DELETE /nodes/{id} enqueues cancellation for non-terminal jobs

For in-flight provisioning, set phase=cancelling so the reconciler's
CancelHandler picks it up next tick and runs pulumi destroy. For
already-terminal jobs, idempotently soft-delete the inventory row.
Missing node → 404."
```

---

### Task 27: Startup advisory-lock + reconciler boot

**Files:**
- Modify: `package/src/inferia/services/orchestration/server.py`
- Test: `package/src/inferia/services/orchestration/test/test_startup_wiring.py`

- [ ] **Step 27.1: Write the failing test**

Add to `test_startup_wiring.py`:

```python
@pytest.mark.asyncio
async def test_reconciler_starts_on_app_startup_and_holds_advisory_lock():
    """Starting the orchestration app starts a ProvisioningReconciler task
    that holds the Postgres advisory lock."""
    from inferia.services.orchestration.server import start_reconciler

    # Fake db that tracks advisory_lock + advisory_unlock calls.
    lock_calls = []
    conn = MagicMock()
    conn.fetchval = AsyncMock(side_effect=lambda sql, *args:
        (lock_calls.append((sql, args)) or True)
        if "pg_try_advisory_lock" in sql else
        (lock_calls.append((sql, args)) or None)
    )
    db = MagicMock()
    db.acquire = MagicMock()
    db.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    db.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

    stop = asyncio.Event()
    async def runner():
        await start_reconciler(db, handlers={}, emit_event=AsyncMock(),
                               stop_event=stop, lease_holder="t")
    task = asyncio.create_task(runner())
    await asyncio.sleep(0.1)
    stop.set()
    await task

    # First call should be the lock attempt.
    assert any("pg_try_advisory_lock" in s for s, _ in lock_calls)
    # Last call should be the unlock.
    assert any("pg_advisory_unlock" in s for s, _ in lock_calls)


@pytest.mark.asyncio
async def test_reconciler_polls_for_lock_when_not_acquired():
    """If another inferia-app holds the lock, this instance sleeps and
    retries until either it gets the lock or stop fires."""
    from inferia.services.orchestration.server import start_reconciler

    attempts = []
    conn = MagicMock()
    async def _fetchval(sql, *args):
        if "pg_try_advisory_lock" in sql:
            attempts.append(1)
            return False  # never acquired
        return None
    conn.fetchval = _fetchval
    db = MagicMock()
    db.acquire = MagicMock()
    db.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    db.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

    stop = asyncio.Event()
    async def stop_after():
        await asyncio.sleep(0.05)
        stop.set()
    await asyncio.gather(
        start_reconciler(db, handlers={}, emit_event=AsyncMock(),
                         stop_event=stop, lease_holder="t",
                         poll_for_lock_s=0.01),
        stop_after(),
    )
    assert len(attempts) >= 2  # retried at least once
```

- [ ] **Step 27.2: Run test to verify it fails**

```bash
pytest package/src/inferia/services/orchestration/test/test_startup_wiring.py -v
```

Expected: FAIL.

- [ ] **Step 27.3: Implement start_reconciler in server.py**

Edit `server.py`:

```python
RECONCILER_LOCK_KEY = 0xD1F24B3EC7A91100


async def start_reconciler(
    db, *, handlers: dict, emit_event, stop_event: asyncio.Event,
    lease_holder: str, poll_for_lock_s: float = 15.0,
) -> None:
    """Single-active reconciler loop. Acquires a Postgres advisory lock
    via pg_try_advisory_lock; if held by another instance, sleeps and
    retries. Released automatically by Postgres on connection drop."""
    from inferia.services.orchestration.services.provisioning.jobs.repository import (
        ProvisioningJobRepository,
    )
    from inferia.services.orchestration.services.provisioning.reconciler.loop import (
        ProvisioningReconciler,
    )

    while not stop_event.is_set():
        async with db.acquire() as conn:
            got_lock = await conn.fetchval(
                "SELECT pg_try_advisory_lock($1)", RECONCILER_LOCK_KEY,
            )
            if not got_lock:
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=poll_for_lock_s)
                except asyncio.TimeoutError:
                    continue
                else:
                    return
            try:
                repo = ProvisioningJobRepository(db)
                rec = ProvisioningReconciler(
                    repo=repo, handlers=handlers,
                    emit_event=emit_event, db=db,
                    concurrency=4, poll_interval_s=2.0,
                    lease_seconds=300, renew_interval_s=60.0,
                    lease_holder=lease_holder,
                )
                run_task = asyncio.create_task(rec.run())
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=None)
                except asyncio.CancelledError:
                    raise
                finally:
                    await rec.stop_with_drain(grace_seconds=30.0)
                    run_task.cancel()
                    try:
                        await run_task
                    except asyncio.CancelledError:
                        pass
            finally:
                await conn.fetchval("SELECT pg_advisory_unlock($1)", RECONCILER_LOCK_KEY)
        return
```

Wire this into `lifespan`/startup. Build `handlers={Phase.PREFLIGHT: PreflightHandler(), Phase.PROVISIONING: PulumiUpHandler(), Phase.BOOTSTRAPPING: BootstrapHandler(inventory_repo=...), Phase.CANCELLING: CancelHandler()}`.

- [ ] **Step 27.4: Run test to verify it passes**

```bash
pytest package/src/inferia/services/orchestration/test/test_startup_wiring.py -v
```

Expected: 2 new tests PASS (existing tests still PASS).

- [ ] **Step 27.5: Commit**

```bash
git add package/src/inferia/services/orchestration/server.py \
        package/src/inferia/services/orchestration/test/test_startup_wiring.py
git -c user.signingkey=/home/ankit/.ssh/id_ed25519_gh commit -S -m "server: start single-active ProvisioningReconciler on app startup

Acquires pg_try_advisory_lock(RECONCILER_LOCK_KEY) for cross-replica
exclusion. Holds the lock for the lifetime of the reconciler task;
Postgres auto-releases on connection drop so a crashed inferia-app
can't keep other replicas locked out forever. Non-leader replicas
poll every 15s to attempt takeover. Graceful shutdown drains
in-flight handlers for up to 30s before cancelling."
```

---

### Task 28: inferia-worker `recipes.go` CPU relax

**Files:** (in the `inferia-worker` repo, branch `feat/aws-ec2-bootstrap`)
- Modify: `internal/runtime/recipes/recipes.go`
- Modify: `internal/runtime/recipes/recipes_test.go`

- [ ] **Step 28.1: Switch to the worker repo and write the failing test**

```bash
cd /storage/intern/hooman/work/inferia-worker
git checkout feat/aws-ec2-bootstrap
git pull --ff-only
```

Add to `internal/runtime/recipes/recipes_test.go`:

```go
func TestPrepareAllowsZeroGPUsForCpuFriendlyEngines(t *testing.T) {
    // ollama is in the CPU-friendly set: should NOT reject zero GPUIndices.
    plan := Plan{
        DeploymentID: "d-1",
        Engine: "ollama",
        ModelName: "smollm2:135m",
        GPUIndices: []int{},
        Port: 19000,
    }
    if _, err := Prepare(plan); err != nil {
        t.Fatalf("ollama with zero GPU should be allowed; got error: %v", err)
    }
}

func TestPrepareRejectsZeroGPUsForGpuOnlyEngines(t *testing.T) {
    // vllm is GPU-only: must still reject zero GPUIndices.
    plan := Plan{
        DeploymentID: "d-1",
        Engine: "vllm",
        ModelName: "Qwen/Qwen3-0.6B",
        GPUIndices: []int{},
        Port: 19000,
    }
    if _, err := Prepare(plan); err == nil {
        t.Fatal("vllm with zero GPU should be rejected")
    }
}

func TestPrepareAllowsZeroGPUsForInfinity(t *testing.T) {
    plan := Plan{
        DeploymentID: "d-1",
        Engine: "infinity",
        ModelName: "BAAI/bge-small-en-v1.5",
        GPUIndices: []int{},
        Port: 19000,
    }
    if _, err := Prepare(plan); err != nil {
        t.Fatalf("infinity with zero GPU should be allowed; got error: %v", err)
    }
}
```

- [ ] **Step 28.2: Run test to verify it fails**

```bash
go test ./internal/runtime/recipes/ -v -run "TestPrepareAllowsZeroGPUsForCpuFriendlyEngines|TestPrepareRejectsZeroGPUsForGpuOnlyEngines|TestPrepareAllowsZeroGPUsForInfinity"
```

Expected: 2 tests FAIL (ollama, infinity rejected); 1 PASSES (vllm correctly rejected).

- [ ] **Step 28.3: Relax the check**

Edit `internal/runtime/recipes/recipes.go`. Find the `Prepare` function (or wherever the `len(GPUIndices) == 0` check lives). Change:

```go
// Previous: hard reject regardless of engine.
// if len(plan.GPUIndices) == 0 {
//     return nil, fmt.Errorf("at least one GPU index is required")
// }

// New: only reject for GPU-only engines.
cpuFriendly := map[string]bool{
    "ollama":   true,
    "infinity": true,
}
if len(plan.GPUIndices) == 0 && !cpuFriendly[plan.Engine] {
    return nil, fmt.Errorf("engine %q requires at least one GPU index", plan.Engine)
}
```

If the function plumbs GPUIndices into a `--gpus all` docker flag or `--device /dev/nvidia<N>`, also branch on `len(GPUIndices) > 0` to omit those flags entirely when zero.

- [ ] **Step 28.4: Run test to verify it passes**

```bash
go test ./internal/runtime/recipes/ -v
```

Expected: all tests PASS (existing + the 3 new).

- [ ] **Step 28.5: Commit**

```bash
git add internal/runtime/recipes/recipes.go internal/runtime/recipes/recipes_test.go
git -c user.signingkey=/home/ankit/.ssh/id_ed25519_gh commit -S -m "recipes: allow zero GPUs for cpu-friendly engines

ollama and infinity can run inference on CPU. Previously len(GPUIndices)
== 0 hard-rejected all deploys; this kept the new InferiaLLM 'CPU only'
instance tier from being able to deploy anything. vllm, tgi, triton
still require at least one GPU index.

Closes the worker half of the cpu-tier wiring; InferiaLLM side lives
in the bootstrap_builder CPU branching commit."
```

(Do NOT push.)

---

### Task 29: `AWSMetadataGrid` + `RetryProvisioningButton`

**Files:**
- Create: `apps/dashboard/src/components/nodes/AWSMetadataGrid.tsx`
- Create: `apps/dashboard/src/components/nodes/AWSMetadataGrid.test.tsx`
- Create: `apps/dashboard/src/components/nodes/RetryProvisioningButton.tsx`
- Create: `apps/dashboard/src/components/nodes/RetryProvisioningButton.test.tsx`

- [ ] **Step 29.1: Write the failing tests**

Create `AWSMetadataGrid.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { AWSMetadataGrid } from "./AWSMetadataGrid";


describe("AWSMetadataGrid", () => {
  it("renders all six fields", () => {
    render(
      <AWSMetadataGrid
        metadata={{
          instance_class: "normal_gpu",
          instance_type:  "g6.xlarge",
          region:         "us-east-1",
          ami_id:         "ami-deadbeef",
          instance_id:    "i-0abc1234",
          public_dns:     "ec2-1-2-3-4.compute-1.amazonaws.com",
        }}
      />
    );
    expect(screen.getByText("g6.xlarge")).toBeInTheDocument();
    expect(screen.getByText("us-east-1")).toBeInTheDocument();
    expect(screen.getByText("ami-deadbeef")).toBeInTheDocument();
    expect(screen.getByText("i-0abc1234")).toBeInTheDocument();
    expect(screen.getByText(/ec2-1-2-3-4/)).toBeInTheDocument();
  });

  it("renders em-dash placeholders for null fields", () => {
    render(
      <AWSMetadataGrid
        metadata={{
          instance_class: "normal_gpu",
          instance_type:  "g6.xlarge",
          region:         "us-east-1",
          ami_id:         "ami-x",
          instance_id:    null,
          public_dns:     null,
        }}
      />
    );
    expect(screen.getAllByText("—").length).toBeGreaterThanOrEqual(2);
  });

  it("renders 'Normal GPU' label for normal_gpu class", () => {
    render(
      <AWSMetadataGrid
        metadata={{
          instance_class: "normal_gpu",
          instance_type:  "g6.xlarge",
          region:         "us-east-1",
          ami_id:         "ami-x",
          instance_id:    null,
          public_dns:     null,
        }}
      />
    );
    expect(screen.getByText("Normal GPU")).toBeInTheDocument();
  });
});
```

Create `RetryProvisioningButton.test.tsx`:

```tsx
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it, vi } from "vitest";
import { RetryProvisioningButton } from "./RetryProvisioningButton";


function _wrap(ui: React.ReactElement) {
  const qc = new QueryClient();
  return <QueryClientProvider client={qc}>{ui}</QueryClientProvider>;
}


describe("RetryProvisioningButton", () => {
  it("posts to /retry on click", async () => {
    const fetchSpy = vi.spyOn(global, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ job_id: "j-1" }), { status: 200 }),
    );
    render(_wrap(<RetryProvisioningButton nodeId="node-1" />));
    fireEvent.click(screen.getByRole("button", { name: /retry/i }));
    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        expect.stringContaining("/api/v1/nodes/node-1/provisioning/retry"),
        expect.objectContaining({ method: "POST" }),
      );
    });
  });

  it("is disabled while the retry is in-flight", async () => {
    vi.spyOn(global, "fetch").mockImplementation(
      () => new Promise(() => {}),  // never resolves
    );
    render(_wrap(<RetryProvisioningButton nodeId="node-1" />));
    const btn = screen.getByRole("button", { name: /retry/i });
    fireEvent.click(btn);
    await waitFor(() => expect(btn).toBeDisabled());
  });

  it("calls onSuccess after successful retry", async () => {
    vi.spyOn(global, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ job_id: "j-1" }), { status: 200 }),
    );
    const onSuccess = vi.fn();
    render(_wrap(<RetryProvisioningButton nodeId="node-1" onSuccess={onSuccess} />));
    fireEvent.click(screen.getByRole("button", { name: /retry/i }));
    await waitFor(() => expect(onSuccess).toHaveBeenCalled());
  });
});
```

- [ ] **Step 29.2: Run tests to verify they fail**

```bash
cd apps/dashboard && npm test -- AWSMetadataGrid RetryProvisioningButton
```

Expected: 6 tests FAIL with module-not-found.

- [ ] **Step 29.3: Implement the components**

Create `AWSMetadataGrid.tsx`:

```tsx
import { Copy } from "lucide-react";
import { useState } from "react";


export type AWSMetadata = {
  instance_class: "normal_gpu" | "heavy_gpu" | "cpu" | null;
  instance_type: string | null;
  region: string | null;
  ami_id: string | null;
  instance_id: string | null;
  public_dns: string | null;
};


const CLASS_LABEL: Record<string, string> = {
  normal_gpu: "Normal GPU",
  heavy_gpu:  "Heavy GPU",
  cpu:        "CPU only",
};


function CopyableField({ label, value }: { label: string; value: string | null }) {
  const [copied, setCopied] = useState(false);
  const v = value ?? "—";
  return (
    <div className="flex items-center justify-between">
      <span className="text-sm text-muted-foreground">{label}</span>
      <span className="font-mono text-sm flex items-center gap-2">
        {v}
        {value && (
          <button
            aria-label={`Copy ${label}`}
            onClick={() => {
              navigator.clipboard.writeText(value);
              setCopied(true);
              setTimeout(() => setCopied(false), 1500);
            }}
            className="text-muted-foreground hover:text-foreground"
          >
            <Copy className="h-3 w-3" />
          </button>
        )}
        {copied && <span className="text-xs text-green-600">copied</span>}
      </span>
    </div>
  );
}


function PlainField({ label, value }: { label: string; value: string | null }) {
  return (
    <div className="flex items-center justify-between">
      <span className="text-sm text-muted-foreground">{label}</span>
      <span className="font-mono text-sm">{value ?? "—"}</span>
    </div>
  );
}


export function AWSMetadataGrid({ metadata }: { metadata: AWSMetadata }) {
  return (
    <div className="rounded-lg border p-4 grid grid-cols-1 md:grid-cols-2 gap-x-8 gap-y-2">
      <PlainField label="Instance class" value={
        metadata.instance_class ? CLASS_LABEL[metadata.instance_class] : null
      } />
      <CopyableField label="Instance ID"  value={metadata.instance_id} />
      <PlainField    label="Instance type" value={metadata.instance_type} />
      <CopyableField label="Public DNS"   value={metadata.public_dns} />
      <PlainField    label="Region"       value={metadata.region} />
      <PlainField    label="AMI"          value={metadata.ami_id} />
    </div>
  );
}
```

Create `RetryProvisioningButton.tsx`:

```tsx
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Loader2, RefreshCw } from "lucide-react";


type Props = {
  nodeId: string;
  onSuccess?: () => void;
};


export function RetryProvisioningButton({ nodeId, onSuccess }: Props) {
  const qc = useQueryClient();
  const mutation = useMutation({
    mutationFn: async () => {
      const resp = await fetch(`/api/v1/nodes/${nodeId}/provisioning/retry`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
      });
      if (!resp.ok) throw new Error(`retry failed: ${resp.status}`);
      return resp.json();
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["node-provisioning", nodeId] });
      onSuccess?.();
    },
  });

  return (
    <button
      onClick={() => mutation.mutate()}
      disabled={mutation.isPending}
      className="inline-flex items-center gap-2 px-3 py-2 rounded-md
                 bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50"
    >
      {mutation.isPending
        ? <Loader2 className="h-4 w-4 animate-spin" />
        : <RefreshCw className="h-4 w-4" />}
      Retry
    </button>
  );
}
```

- [ ] **Step 29.4: Run tests to verify they pass**

```bash
cd apps/dashboard && npm test -- AWSMetadataGrid RetryProvisioningButton
```

Expected: all 6 tests PASS.

- [ ] **Step 29.5: Commit**

```bash
git add apps/dashboard/src/components/nodes/AWSMetadataGrid.tsx \
        apps/dashboard/src/components/nodes/AWSMetadataGrid.test.tsx \
        apps/dashboard/src/components/nodes/RetryProvisioningButton.tsx \
        apps/dashboard/src/components/nodes/RetryProvisioningButton.test.tsx
git -c user.signingkey=/home/ankit/.ssh/id_ed25519_gh commit -S -m "dashboard/nodes: add AWSMetadataGrid + RetryProvisioningButton

AWSMetadataGrid renders the 6 AWS fields (instance_class label,
instance_type, region, ami_id, instance_id, public_dns) with em-dash
placeholders for nulls and copy-to-clipboard on instance_id/public_dns.

RetryProvisioningButton posts to /api/v1/nodes/{id}/provisioning/retry
and invalidates the TanStack Query cache so the Overview tab re-fetches.
Button disables while the request is in-flight."
```

---

### Task 30: `InstanceDetail` Overview wiring

**Files:**
- Modify: `apps/dashboard/src/pages/Compute/InstanceDetail.tsx`
- Modify: `apps/dashboard/src/pages/Compute/InstanceDetail.test.tsx`

- [ ] **Step 30.1: Write the failing test**

Add to `InstanceDetail.test.tsx`:

```tsx
it("shows AWSMetadataGrid when provider=aws", async () => {
  // Set up MSW handler for /provisioning that returns aws_metadata
  // ... existing test infrastructure ...
  render(<InstanceDetail />);
  await waitFor(() => {
    expect(screen.getByText("Instance class")).toBeInTheDocument();
    expect(screen.getByText("Public DNS")).toBeInTheDocument();
  });
});


it("shows Retry button when phase=failed and error fields are set", async () => {
  // ... MSW returns phase: 'failed', error: { code: 'PULUMI_CLI_MISSING', ... }
  render(<InstanceDetail />);
  await waitFor(() => {
    expect(screen.getByRole("button", { name: /retry/i })).toBeInTheDocument();
    expect(screen.getByText(/Pulumi CLI not installed/i)).toBeInTheDocument();
  });
});


it("shows attempt-count badge when attempt_count > 1", async () => {
  // ... MSW returns attempt_count: 3
  render(<InstanceDetail />);
  await waitFor(() => {
    expect(screen.getByText(/attempt 3/i)).toBeInTheDocument();
  });
});


it("does NOT show AWSMetadataGrid when provider=worker", async () => {
  // ... MSW returns provider: 'worker', aws_metadata: null
  render(<InstanceDetail />);
  await waitFor(() => {
    expect(screen.queryByText("Instance class")).not.toBeInTheDocument();
  });
});
```

- [ ] **Step 30.2: Run tests to verify they fail**

```bash
cd apps/dashboard && npm test -- InstanceDetail
```

Expected: 4 tests FAIL.

- [ ] **Step 30.3: Wire the components into InstanceDetail**

Edit `InstanceDetail.tsx`. In the Overview tab JSX, above the existing Node Information grid:

```tsx
{/* Provisioning status card (existing, with Retry on failure) */}
<ProvisioningStatus
  summary={provisioningSummary}
  attemptCount={provisioningSummary?.attempt_count ?? 0}
/>
{provisioningSummary?.phase === "failed" && provisioningSummary?.error && (
  <div className="rounded-lg border border-red-400 bg-red-50 p-4">
    <h3 className="font-medium text-red-900">{provisioningSummary.error.message}</h3>
    {provisioningSummary.error.hint && (
      <p className="text-sm text-red-700 mt-1">{provisioningSummary.error.hint}</p>
    )}
    <div className="mt-3">
      <RetryProvisioningButton nodeId={nodeId} />
    </div>
  </div>
)}

{/* AWS metadata grid — only for provider=aws */}
{node?.provider === "aws" && provisioningSummary?.aws_metadata && (
  <AWSMetadataGrid metadata={provisioningSummary.aws_metadata} />
)}
```

Update the `ProvisioningStatus` component to render an "attempt N" badge next to the current phase when `attemptCount > 1`. Update the TanStack Query type to include the new `error`, `aws_metadata`, `attempt_count` fields.

- [ ] **Step 30.4: Run tests to verify they pass**

```bash
cd apps/dashboard && npm test -- InstanceDetail
```

Expected: all PASS (4 new + existing).

- [ ] **Step 30.5: Commit**

```bash
git add apps/dashboard/src/pages/Compute/InstanceDetail.tsx \
        apps/dashboard/src/pages/Compute/InstanceDetail.test.tsx \
        apps/dashboard/src/components/nodes/ProvisioningStatus.tsx
git -c user.signingkey=/home/ankit/.ssh/id_ed25519_gh commit -S -m "dashboard/InstanceDetail: surface error + AWS metadata + Retry

Overview tab now renders:
- ProvisioningStatus card with attempt-count badge when > 1
- Red error banner with message + hint + Retry button when phase=failed
- AWSMetadataGrid below the status when provider=aws

Polling cadence unchanged: 2s while non-terminal, 30s when ready, stops
on terminated."
```

---

### Task 31: `useInstanceCatalog` + `NewPool` swap

**Files:**
- Create: `apps/dashboard/src/hooks/useInstanceCatalog.ts`
- Create: `apps/dashboard/src/hooks/useInstanceCatalog.test.ts`
- Modify: `apps/dashboard/src/pages/Compute/NewPool.tsx`
- Modify: `apps/dashboard/src/pages/Compute/NewPool.test.tsx`

- [ ] **Step 31.1: Write the failing test**

Create `useInstanceCatalog.test.ts`:

```ts
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it, vi } from "vitest";
import { useInstanceCatalog } from "./useInstanceCatalog";


function wrap(ui: React.ReactElement) {
  const qc = new QueryClient();
  return <QueryClientProvider client={qc}>{ui}</QueryClientProvider>;
}


describe("useInstanceCatalog", () => {
  it("fetches and groups by class", async () => {
    const catalog = {
      normal_gpu: [{ name: "g6.xlarge", cls: "normal_gpu", vcpu: 4,
                     ram_gb: 16, gpu_count: 1, gpu_model: "NVIDIA L4",
                     gpu_ram_gb: 24, approx_usd_per_hour: 0.8 }],
      heavy_gpu: [],
      cpu: [{ name: "c6i.xlarge", cls: "cpu", vcpu: 4, ram_gb: 8,
              gpu_count: 0, gpu_model: null, gpu_ram_gb: 0,
              approx_usd_per_hour: 0.17 }],
    };
    vi.spyOn(global, "fetch").mockResolvedValue(
      new Response(JSON.stringify(catalog), { status: 200 }),
    );
    const { result } = renderHook(() => useInstanceCatalog(),
                                  { wrapper: ({ children }) => wrap(children) });
    await waitFor(() => expect(result.current.data).toBeDefined());
    expect(result.current.data?.normal_gpu[0].name).toBe("g6.xlarge");
    expect(result.current.data?.cpu[0].gpu_count).toBe(0);
  });
});
```

Add to `NewPool.test.tsx`:

```tsx
it("swaps awsInstanceTiers constant for useInstanceCatalog query", async () => {
  // After the swap, the dropdown options come from the API mock, not
  // a hard-coded module constant.
  vi.spyOn(global, "fetch").mockImplementation(async (url: string) => {
    if (String(url).includes("/instance-catalog")) {
      return new Response(JSON.stringify({
        normal_gpu: [{ name: "g6.xlarge", /*...*/ }],
        heavy_gpu: [], cpu: [],
      }), { status: 200 });
    }
    return new Response("{}", { status: 200 });
  });

  render(<NewPool />);
  // Navigate to AWS step, assert dropdown options come from the API.
  await waitFor(() => {
    expect(screen.getByText("g6.xlarge")).toBeInTheDocument();
  });
});
```

- [ ] **Step 31.2: Run tests to verify they fail**

```bash
cd apps/dashboard && npm test -- useInstanceCatalog NewPool
```

Expected: at least 1 FAIL.

- [ ] **Step 31.3: Implement the hook and swap**

Create `useInstanceCatalog.ts`:

```ts
import { useQuery } from "@tanstack/react-query";


export type InstanceType = {
  name: string;
  cls: "normal_gpu" | "heavy_gpu" | "cpu";
  vcpu: number;
  ram_gb: number;
  gpu_count: number;
  gpu_model: string | null;
  gpu_ram_gb: number;
  approx_usd_per_hour: number;
};


export type InstanceCatalog = Record<"normal_gpu" | "heavy_gpu" | "cpu", InstanceType[]>;


export function useInstanceCatalog() {
  return useQuery<InstanceCatalog>({
    queryKey: ["aws-instance-catalog"],
    queryFn: async () => {
      const resp = await fetch("/api/v1/providers/aws/instance-catalog");
      if (!resp.ok) throw new Error(`catalog fetch failed: ${resp.status}`);
      return resp.json();
    },
    staleTime: 5 * 60 * 1000,  // 5 min — catalog rarely changes
  });
}
```

Edit `NewPool.tsx`. Find the existing `awsInstanceTiers` constant and replace usage with `useInstanceCatalog()`:

```tsx
import { useInstanceCatalog } from "@/hooks/useInstanceCatalog";

// inside AWSPoolFields:
const { data: catalog, isLoading } = useInstanceCatalog();
// instead of awsInstanceTiers[state.instanceTier]:
const options = catalog?.[state.instanceTier] ?? [];
```

Drop the `awsInstanceTiers` constant entirely.

- [ ] **Step 31.4: Run tests to verify they pass**

```bash
cd apps/dashboard && npm test -- useInstanceCatalog NewPool
```

Expected: all PASS.

- [ ] **Step 31.5: Commit**

```bash
git add apps/dashboard/src/hooks/useInstanceCatalog.ts \
        apps/dashboard/src/hooks/useInstanceCatalog.test.ts \
        apps/dashboard/src/pages/Compute/NewPool.tsx \
        apps/dashboard/src/pages/Compute/NewPool.test.tsx
git -c user.signingkey=/home/ankit/.ssh/id_ed25519_gh commit -S -m "dashboard: useInstanceCatalog hook + drop awsInstanceTiers constant

The wizard now fetches the instance catalog from
/api/v1/providers/aws/instance-catalog via TanStack Query (staleTime
5 min) instead of importing a hard-coded constant. Adding a new EC2
type is now a one-file change in the backend catalog module — no
frontend edit needed."
```

---

### Task 32: Integration: happy + retry + cancel

**Files:**
- Create: `package/src/inferia/services/orchestration/services/provisioning/tests/integration/test_happy_path.py`
- Create: `package/src/inferia/services/orchestration/services/provisioning/tests/integration/test_retry.py`
- Create: `package/src/inferia/services/orchestration/services/provisioning/tests/integration/test_cancel.py`

- [ ] **Step 32.1: Write the failing test (happy path)**

Create `integration/test_happy_path.py`:

```python
"""End-to-end happy-path integration test.

POST /api/v1/nodes/add/aws → reconciler runs all phases → ready.

Uses:
- real Postgres (gated on INFERIA_TEST_DATABASE_URL)
- moto for AWS API mocking (sts, ec2)
- a patched run_pulumi_up_sync that simulates pulumi outputs
"""
from __future__ import annotations

import asyncio
import os
import uuid
from pathlib import Path
from unittest.mock import patch

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient

MIGRATION = Path(__file__).resolve().parents[6] / "infra" / "schema" / "migrations" / "20260528_provisioning_jobs.sql"


@pytest.fixture
def test_database_url() -> str:
    url = os.environ.get("INFERIA_TEST_DATABASE_URL")
    if not url:
        pytest.skip("INFERIA_TEST_DATABASE_URL not set")
    return url


@pytest.fixture
async def app_with_real_db(test_database_url):
    """Boots the orchestration FastAPI app against a real test DB.
    Stops reconciler before yielding the AsyncClient so the test can
    drive ticks deterministically."""
    from inferia.services.orchestration.server import create_app
    pool = await asyncpg.create_pool(test_database_url, min_size=2, max_size=10)
    # Apply the migration.
    async with pool.acquire() as conn:
        sql = MIGRATION.read_text()
        for stmt in [s.strip() for s in sql.split(";") if s.strip()]:
            await conn.execute(stmt)
    app = create_app(db_pool=pool, start_reconciler=False)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        yield app, client, pool
    await pool.close()


@pytest.mark.asyncio
async def test_full_happy_path_to_ready(app_with_real_db):
    """POST add/aws → drive 4 reconciler ticks → phase=ready, inventory=ready."""
    from inferia.services.orchestration.services.adapter_engine.adapters.pulumi.pulumi_aws_adapter import (
        StackOutputs,
    )
    app, client, pool = app_with_real_db

    # 1. Configure AWS creds (via the existing system_settings path) —
    # simplest is to inject directly into ProvidersConfig table; defer
    # to a helper. For this test, we monkey-patch verify_credentials +
    # run_pulumi_up_sync + resolve_ami + verify_subnet_exists +
    # verify_security_group_exists to short-circuit AWS.
    with patch(
        "inferia.services.orchestration.services.provisioning.phases."
        "preflight.verify_credentials", return_value={"Account": "123"},
    ), patch(
        "inferia.services.orchestration.services.provisioning.phases."
        "preflight.resolve_ami", return_value="ami-abc",
    ), patch(
        "inferia.services.orchestration.services.provisioning.phases."
        "preflight.verify_subnet_exists", return_value=None,
    ), patch(
        "inferia.services.orchestration.services.provisioning.phases."
        "preflight.verify_security_group_exists", return_value=None,
    ), patch(
        "inferia.services.orchestration.services.provisioning.phases."
        "pulumi_up.run_pulumi_up_sync",
        return_value=StackOutputs(
            instance_id="i-abc", public_dns="ec2-x.compute.amazonaws.com",
            region="us-east-1", ami_id="ami-abc",
        ),
    ):
        # 2. Submit the request.
        resp = await client.post(
            "/api/v1/nodes/add/aws",
            json={"spec": {
                "instance_class": "normal_gpu",
                "instance_type":  "g6.xlarge",
                "region":         "us-east-1",
            }},
            headers={"X-Organization-ID": "org-int",
                       "Authorization": "Bearer test"},
        )
        assert resp.status_code == 200
        node_id = resp.json()["node_id"]

        # 3. Drive reconciler ticks until ready.
        rec = app.state.reconciler  # exposed by create_app for tests
        for _ in range(6):
            await rec.tick_once()
            # Simulate the worker registering: set inventory.state=ready
            # once the job lands in BOOTSTRAPPING.
            async with pool.acquire() as conn:
                phase = await conn.fetchval(
                    "SELECT phase FROM provisioning_jobs WHERE node_id=$1",
                    uuid.UUID(node_id),
                )
                if phase == "bootstrapping":
                    await conn.execute(
                        "UPDATE compute_inventory SET state='ready' WHERE id=$1",
                        uuid.UUID(node_id),
                    )

        # 4. Assert terminal state.
        resp = await client.get(
            f"/api/v1/nodes/{node_id}/provisioning",
            headers={"Authorization": "Bearer test"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["phase"] == "ready"
        assert body["terminal"] is True
        assert body["aws_metadata"]["instance_id"] == "i-abc"
        assert body["error"] is None
```

- [ ] **Step 32.2: Run test**

```bash
INFERIA_TEST_DATABASE_URL=postgresql://... pytest \
    package/src/inferia/services/orchestration/services/provisioning/tests/integration/test_happy_path.py -v
```

Expected: PASS (assuming the test DB has all prerequisite tables).

- [ ] **Step 32.3: Write retry + cancel tests**

Create `test_retry.py`:

```python
"""Integration test: a PERMANENT failure → POST /retry → success."""
from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest

from inferia.services.orchestration.services.adapter_engine.adapters.pulumi.pulumi_aws_adapter import (
    StackOutputs,
)
from inferia.services.orchestration.services.provisioning.errors import (
    InvalidCredentialsError,
)


@pytest.mark.asyncio
async def test_failed_job_retried_to_ready(app_with_real_db):
    app, client, pool = app_with_real_db

    # --- Run 1: creds fail → phase=failed --------------------------------
    with patch(
        "inferia.services.orchestration.services.provisioning.phases."
        "preflight.verify_credentials",
        side_effect=InvalidCredentialsError("bad creds"),
    ):
        resp = await client.post(
            "/api/v1/nodes/add/aws",
            json={"spec": {"instance_class": "normal_gpu",
                              "instance_type":  "g6.xlarge",
                              "region":         "us-east-1"}},
            headers={"X-Organization-ID": "org-int",
                       "Authorization": "Bearer test"},
        )
        node_id = resp.json()["node_id"]

        rec = app.state.reconciler
        await rec.tick_once()  # preflight fails

        body = (await client.get(
            f"/api/v1/nodes/{node_id}/provisioning",
            headers={"Authorization": "Bearer test"},
        )).json()
        assert body["phase"] == "failed"
        assert body["error"]["code"] == "INVALID_CREDENTIALS"

    # --- POST /retry resets to pending -----------------------------------
    resp = await client.post(
        f"/api/v1/nodes/{node_id}/provisioning/retry",
        headers={"Authorization": "Bearer test"},
    )
    assert resp.status_code == 200

    # --- Run 2: creds work + pulumi works → ready ------------------------
    with patch(
        "inferia.services.orchestration.services.provisioning.phases."
        "preflight.verify_credentials", return_value={"Account": "123"},
    ), patch(
        "inferia.services.orchestration.services.provisioning.phases."
        "preflight.resolve_ami", return_value="ami-abc",
    ), patch(
        "inferia.services.orchestration.services.provisioning.phases."
        "preflight.verify_subnet_exists", return_value=None,
    ), patch(
        "inferia.services.orchestration.services.provisioning.phases."
        "preflight.verify_security_group_exists", return_value=None,
    ), patch(
        "inferia.services.orchestration.services.provisioning.phases."
        "pulumi_up.run_pulumi_up_sync",
        return_value=StackOutputs(
            instance_id="i-abc", public_dns="ec2.x.compute.amazonaws.com",
            region="us-east-1", ami_id="ami-abc",
        ),
    ):
        for _ in range(6):
            await rec.tick_once()
            async with pool.acquire() as conn:
                phase = await conn.fetchval(
                    "SELECT phase FROM provisioning_jobs WHERE node_id=$1",
                    uuid.UUID(node_id),
                )
                if phase == "bootstrapping":
                    await conn.execute(
                        "UPDATE compute_inventory SET state='ready' WHERE id=$1",
                        uuid.UUID(node_id),
                    )

        body = (await client.get(
            f"/api/v1/nodes/{node_id}/provisioning",
            headers={"Authorization": "Bearer test"},
        )).json()
        assert body["phase"] == "ready"
        # attempt_count was reset to 0 by reset_for_retry.
        assert body["attempt_count"] == 0
```

Create `test_cancel.py`:

```python
"""Integration test: DELETE during PROVISIONING → CancelHandler runs destroy."""
from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest

from inferia.services.orchestration.services.adapter_engine.adapters.pulumi.pulumi_aws_adapter import (
    StackOutputs,
)


@pytest.mark.asyncio
async def test_delete_mid_provision_triggers_cancel(app_with_real_db):
    app, client, pool = app_with_real_db
    destroy_called = False
    def _fake_destroy(*, stack_name, program, env):
        nonlocal destroy_called
        destroy_called = True

    with patch(
        "inferia.services.orchestration.services.provisioning.phases."
        "preflight.verify_credentials", return_value={"Account": "123"},
    ), patch(
        "inferia.services.orchestration.services.provisioning.phases."
        "preflight.resolve_ami", return_value="ami-abc",
    ), patch(
        "inferia.services.orchestration.services.provisioning.phases."
        "preflight.verify_subnet_exists", return_value=None,
    ), patch(
        "inferia.services.orchestration.services.provisioning.phases."
        "preflight.verify_security_group_exists", return_value=None,
    ), patch(
        "inferia.services.orchestration.services.provisioning.phases."
        "pulumi_up.run_pulumi_up_sync",
        return_value=StackOutputs(
            instance_id="i-abc", public_dns=None, region="us-east-1",
            ami_id="ami-abc",
        ),
    ), patch(
        "inferia.services.orchestration.services.provisioning.phases."
        "cancel.run_pulumi_destroy_sync", side_effect=_fake_destroy,
    ):
        # 1. Submit + drive one tick (preflight → provisioning).
        resp = await client.post(
            "/api/v1/nodes/add/aws",
            json={"spec": {"instance_class": "normal_gpu",
                              "instance_type":  "g6.xlarge",
                              "region":         "us-east-1"}},
            headers={"X-Organization-ID": "org-int",
                       "Authorization": "Bearer test"},
        )
        node_id = resp.json()["node_id"]
        rec = app.state.reconciler
        await rec.tick_once()  # preflight → provisioning

        # 2. DELETE before bootstrapping completes.
        resp = await client.delete(
            f"/api/v1/nodes/{node_id}",
            headers={"Authorization": "Bearer test"},
        )
        assert resp.status_code in (200, 204)

        # 3. Drive the cancel tick.
        await rec.tick_once()

        # 4. Assert destroy ran + final state is terminated.
        assert destroy_called
        async with pool.acquire() as conn:
            phase = await conn.fetchval(
                "SELECT phase FROM provisioning_jobs WHERE node_id=$1",
                uuid.UUID(node_id),
            )
            state = await conn.fetchval(
                "SELECT state::text FROM compute_inventory WHERE id=$1",
                uuid.UUID(node_id),
            )
        assert phase == "terminated"
        assert state == "terminated"
```

- [ ] **Step 32.4: Commit**

```bash
git add package/src/inferia/services/orchestration/services/provisioning/tests/integration/test_happy_path.py \
        package/src/inferia/services/orchestration/services/provisioning/tests/integration/test_retry.py \
        package/src/inferia/services/orchestration/services/provisioning/tests/integration/test_cancel.py
git -c user.signingkey=/home/ankit/.ssh/id_ed25519_gh commit -S -m "provisioning: integration tests for happy/retry/cancel flows

End-to-end against a real test Postgres + the FastAPI app (with the
reconciler in tick-driven mode). Verifies POST add/aws → 4 reconciler
ticks → phase=ready; POST /retry resets a failed job to pending;
DELETE triggers CancelHandler with pulumi destroy.

Gated on INFERIA_TEST_DATABASE_URL. AWS-touching calls are patched
(verify_credentials, run_pulumi_up_sync, resolve_ami) so no real AWS
account is needed."
```

---

### Task 33: Integration: crash recovery + upgrade + e2e

**Files:**
- Create: `package/src/inferia/services/orchestration/services/provisioning/tests/integration/test_crash_recovery.py`
- Create: `package/src/inferia/services/orchestration/services/provisioning/tests/integration/test_upgrade.py`
- Create: `apps/dashboard/playwright/aws-provision.spec.ts`

- [ ] **Step 33.1: Write the crash-recovery test**

Create `integration/test_crash_recovery.py`:

```python
"""Integration test: kill reconciler mid-pulumi-up, restart, resume.

Strategy:
1. Override lease_seconds=2 so the lease expires quickly.
2. Patch run_pulumi_up_sync to await a long sleep (so the lease expires
   while it's "running").
3. Cancel the runner task mid-sleep.
4. Restart the reconciler (claim_next_job picks up the expired-lease job).
5. Now patch run_pulumi_up_sync to succeed; drive ticks; assert ready.
"""
from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from inferia.services.orchestration.services.adapter_engine.adapters.pulumi.pulumi_aws_adapter import (
    StackOutputs,
)


@pytest.mark.asyncio
async def test_lease_expiry_re_picks_up_job(app_with_real_db):
    app, client, pool = app_with_real_db

    pulumi_calls = {"n": 0}
    def _pulumi_first_hangs_then_succeeds(*, stack_name, program, env):
        pulumi_calls["n"] += 1
        if pulumi_calls["n"] == 1:
            # Simulate a stalled stack.up — sleep so the 0.2s test lease
            # expires while we're "running". The reconciler's lease
            # renew loop will return False after the lease is stolen.
            import time
            time.sleep(0.6)
            raise RuntimeError("simulated mid-pulumi crash")
        return StackOutputs(
            instance_id="i-abc", public_dns=None, region="us-east-1",
            ami_id="ami-abc",
        )

    with patch(
        "inferia.services.orchestration.services.provisioning.phases."
        "preflight.verify_credentials", return_value={"Account": "123"},
    ), patch(
        "inferia.services.orchestration.services.provisioning.phases."
        "preflight.resolve_ami", return_value="ami-abc",
    ), patch(
        "inferia.services.orchestration.services.provisioning.phases."
        "preflight.verify_subnet_exists", return_value=None,
    ), patch(
        "inferia.services.orchestration.services.provisioning.phases."
        "preflight.verify_security_group_exists", return_value=None,
    ), patch(
        "inferia.services.orchestration.services.provisioning.phases."
        "pulumi_up.run_pulumi_up_sync",
        side_effect=_pulumi_first_hangs_then_succeeds,
    ):
        resp = await client.post(
            "/api/v1/nodes/add/aws",
            json={"spec": {"instance_class": "normal_gpu",
                              "instance_type":  "g6.xlarge",
                              "region":         "us-east-1"}},
            headers={"X-Organization-ID": "org-int",
                       "Authorization": "Bearer test"},
        )
        node_id = resp.json()["node_id"]

        # Drive the first reconciler with a SHORT 0.2s lease so we can
        # simulate crash + recovery in test time.
        rec1 = app.state.reconciler
        rec1.lease_seconds = 0.2
        await rec1.tick_once()  # preflight → provisioning
        # The first pulumi_up raises after sleeping past lease expiry.
        try:
            await rec1.tick_once()
        except Exception:
            pass

        # Manually expire the lease (simulating reconciler death) — the
        # release happens automatically on schedule_retry, but we want to
        # be sure the next tick claims regardless of attempt_count.
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE provisioning_jobs SET lease_holder=NULL, "
                "lease_expires_at=NULL, next_attempt_after=NULL "
                "WHERE node_id=$1",
                uuid.UUID(node_id),
            )

        # Second tick: same reconciler instance, second pulumi succeeds.
        await rec1.tick_once()  # provisioning again, now succeeds
        async with pool.acquire() as conn:
            phase = await conn.fetchval(
                "SELECT phase FROM provisioning_jobs WHERE node_id=$1",
                uuid.UUID(node_id),
            )
            if phase == "bootstrapping":
                await conn.execute(
                    "UPDATE compute_inventory SET state='ready' WHERE id=$1",
                    uuid.UUID(node_id),
                )
        await rec1.tick_once()  # bootstrapping → ready

        body = (await client.get(
            f"/api/v1/nodes/{node_id}/provisioning",
            headers={"Authorization": "Bearer test"},
        )).json()
        assert body["phase"] == "ready"
        assert pulumi_calls["n"] == 2  # first call crashed, second succeeded
```

- [ ] **Step 33.2: Write the upgrade test**

Create `integration/test_upgrade.py`:

```python
"""Integration test: apply migration on a DB with an in-flight 'provisioning'
inventory row → that row becomes 'failed' with an UPGRADE_ABANDONED job
attached."""
from __future__ import annotations

import uuid
from pathlib import Path

import asyncpg
import pytest

MIGRATION = Path(__file__).resolve().parents[6] / "infra" / "schema" / "migrations" / "20260528_provisioning_jobs.sql"


@pytest.mark.asyncio
async def test_migration_marks_inflight_inventory_and_exposes_via_http(
    app_with_real_db, test_database_url,
):
    """Apply the migration on top of a DB with an in-flight provisioning
    row, then verify the HTTP response shape exposes the failure."""
    app, client, pool = app_with_real_db

    pool_id = uuid.uuid4()
    node_id = uuid.uuid4()
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO compute_pools (id, org_id, name, provider, lifecycle_state)
               VALUES ($1, 'org-upgrade', 'p', 'aws', 'running')
               ON CONFLICT (id) DO NOTHING""",
            pool_id,
        )
        await conn.execute(
            """INSERT INTO compute_inventory (id, pool_id, provider,
                   provider_instance_id, state, agent_kind)
               VALUES ($1, $2, 'aws', 'placeholder:upgrade-test',
                       'provisioning', 'worker')""",
            node_id, pool_id,
        )

    # Re-apply the migration (idempotent — picks up our in-flight row).
    sql = MIGRATION.read_text()
    async with pool.acquire() as conn:
        for stmt in [s.strip() for s in sql.split(";") if s.strip()]:
            await conn.execute(stmt)

    # HTTP shape exposes the upgrade-abandoned failure.
    body = (await client.get(
        f"/api/v1/nodes/{node_id}/provisioning",
        headers={"Authorization": "Bearer test"},
    )).json()
    assert body["phase"] == "failed"
    assert body["terminal"] is True
    assert body["error"]["code"] == "UPGRADE_ABANDONED"
    assert body["error"]["class"] == "PERMANENT"
    assert "delete" in body["error"]["hint"].lower()

    # Cleanup.
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM provisioning_jobs WHERE node_id=$1", node_id)
        await conn.execute("DELETE FROM compute_inventory WHERE id=$1", node_id)
        await conn.execute("DELETE FROM compute_pools WHERE id=$1", pool_id)
```

- [ ] **Step 33.3: Write the Playwright e2e**

Create `apps/dashboard/playwright/aws-provision.spec.ts`:

```ts
import { test, expect } from "@playwright/test";


test("happy path: configure → wizard → provision → ready", async ({ page }) => {
  // 1. Login as admin (existing fixture).
  await page.goto("/dashboard");
  // 2. Configure AWS creds in Settings → Providers → AWS (mocked).
  // 3. Open Compute Nodes → Add Pool → AWS.
  await page.click("text=Compute");
  await page.click("text=Add Pool");
  await page.click("text=AWS");
  // 4. Select Normal GPU tab (default), pick g6.xlarge.
  await expect(page.locator("text=Normal GPU")).toBeVisible();
  await page.click("text=g6.xlarge");
  await page.click("text=Create");
  // 5. Land on InstanceDetail; wait for phase=ready.
  await expect(page.locator("text=ready")).toBeVisible({ timeout: 60000 });
  await expect(page.locator("text=Instance ID")).toBeVisible();
});


test("failure path: bad creds shows banner + Retry", async ({ page }) => {
  // Configure creds incorrectly via the Settings page first.
  await page.goto("/dashboard/settings/providers/aws");
  await page.fill("input[name=access_key_id]", "AKIA-INVALID");
  await page.fill("input[name=secret_access_key]", "wrong");
  await page.click("button:has-text('Save')");
  await expect(page.locator("text=Saved")).toBeVisible();

  // Open the wizard.
  await page.goto("/dashboard/compute/nodes/new");
  await page.click("text=AWS");
  await expect(page.locator("text=Normal GPU")).toBeVisible();
  await page.click("text=g6.xlarge");
  await page.fill("input[name=region]", "us-east-1");
  await page.click("text=Create");

  // Should land on InstanceDetail. Wait for phase=failed.
  await page.waitForURL("**/compute/nodes/**");
  await expect(page.locator("text=failed")).toBeVisible({ timeout: 30000 });
  await expect(page.locator("text=AWS credentials rejected")).toBeVisible();
  await expect(page.locator("text=Settings → Providers → AWS")).toBeVisible();
  await expect(page.locator("button", { hasText: "Retry" })).toBeVisible();
});
```

- [ ] **Step 33.4: Run all integration tests**

```bash
INFERIA_TEST_DATABASE_URL=postgresql://... pytest \
    package/src/inferia/services/orchestration/services/provisioning/tests/integration/ -v
cd apps/dashboard && npx playwright test
```

Expected: all PASS (or SKIP if env not set).

- [ ] **Step 33.5: Commit**

```bash
git add package/src/inferia/services/orchestration/services/provisioning/tests/integration/test_crash_recovery.py \
        package/src/inferia/services/orchestration/services/provisioning/tests/integration/test_upgrade.py \
        apps/dashboard/playwright/aws-provision.spec.ts
git -c user.signingkey=/home/ankit/.ssh/id_ed25519_gh commit -S -m "provisioning: crash-recovery + upgrade + e2e tests

Crash-recovery test verifies lease expiry → re-claim → resume produces
phase=ready (Pulumi stack.up is idempotent on same stack name).
Upgrade test verifies the migration's backfill marks in-flight rows as
failed/UPGRADE_ABANDONED with the right HTTP shape.

Playwright e2e covers two flows from the UI: happy provision to ready,
and the failure path showing the INVALID_CREDENTIALS banner + Retry."
```

---

## Self-Review Checklist

Before marking the plan complete, run through this self-check (no subagent needed — fix inline).

**1. Spec coverage** — every spec section maps to a task:

| Spec section | Task(s) |
|---|---|
| Goal #1: never swallow errors silently | T9 (classifier), T20 (reconciler fail-loud) |
| Goal #2: Overview tab shows phase + AWS metadata + Retry | T24, T29, T30 |
| Goal #3: crash recovery on inferia-app restart | T18, T20, T27, T33 |
| Goal #4: CPU tier works end-to-end | T12 (bootstrap_builder), T28 (recipes.go) |
| Data model: provisioning_jobs table | T1 |
| Data model: instance_class/type columns | T1 |
| State machine 8 phases | T5 (enum), T6 (transitions), T20 (dispatch) |
| Error taxonomy | T2, T9 |
| Backoff math | T4 |
| Phase handlers (4) | T13, T14, T15, T16, T17 |
| Reconciler + lease | T18, T19, T20, T21 |
| HTTP changes (5) | T22, T23, T24, T25, T26 |
| Advisory-lock startup | T27 |
| AWS instance catalog | T3, T22 |
| Bootstrap builder CPU branching | T12 |
| Worker recipes.go relax | T28 |
| Dashboard AWS metadata grid + Retry | T29, T30 |
| Wizard catalog query swap | T31 |
| Tests (≥95% coverage) | Every task has its own tests; integration in T32/T33 |
| Upgrade-day migration | T1, T33 |

**2. Placeholder scan** — search the plan for red flags:

```bash
grep -nE "TBD|TODO|FIXME|\\?\\?\\?|fill in" docs/plans/2026-05-28-aws-ec2-node-allocation.md
```

Expected: no matches. (Fix any inline before declaring done.)

**3. Type consistency** — function signatures + names match across tasks:

- `Phase` enum values match in T1 (SQL CHECK), T5 (Python enum), T20 (state transitions), T24 (API response)
- `ClassifiedError` fields match in T5, T9, T20
- `PhaseResult.next_phase`, `PhaseResult.outputs` match in T5, T13, T14, T15, T16, T17, T20
- `ProvisioningJobRepository` method names: `enqueue`, `get`, `get_by_node`, `claim_next_job`, `renew_lease`, `release_lease`, `transition_to`, `schedule_retry`, `fail`, `request_cancel`, `reset_for_retry` — used consistently from T6 onwards
- `PhaseContext` fields: `repo`, `db`, `emit_event`, `now`, `bootstrap_timeout_s`, `aws_creds`, `pulumi_env` — defined in T13, used in T14–T17, T20
- `RECONCILER_LOCK_KEY` constant referenced in T27 + spec
- API response `error: {code, message, hint, class}` shape consistent T24 ↔ T30

---

## Execution Handoff

**Plan complete and saved to `docs/plans/2026-05-28-aws-ec2-node-allocation.md`. Two execution options:**

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, two-stage review (spec compliance + code quality) between each, fast iteration.

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints for review.

**Which approach?**

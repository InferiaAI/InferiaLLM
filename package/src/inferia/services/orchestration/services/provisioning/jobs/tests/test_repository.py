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
    # Regression: jobs must enqueue in 'preflight' (not 'pending') — there
    # is no handler for the 'pending' phase, so a pending job is claimed and
    # immediately fails UNCLASSIFIED. PreflightHandler is the first phase.
    assert "'preflight'" in sql
    assert "'pending'" not in sql


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


@pytest.mark.asyncio
async def test_transition_to_returns_true_when_update_affects_row():
    conn = MagicMock()
    conn.execute = AsyncMock(return_value="UPDATE 1")
    repo = ProvisioningJobRepository(_make_db_with_conn(conn))
    ok = await repo.transition_to(
        job_id=uuid.uuid4(), current_phase=Phase.PREFLIGHT,
        next_phase=Phase.PROVISIONING, lease_holder="me",
    )
    assert ok is True


@pytest.mark.asyncio
async def test_transition_to_returns_false_when_stale_phase():
    """Concurrent reconciler already transitioned this row — our
    phase guard misses, 0 rows affected, return False."""
    conn = MagicMock()
    conn.execute = AsyncMock(return_value="UPDATE 0")
    repo = ProvisioningJobRepository(_make_db_with_conn(conn))
    ok = await repo.transition_to(
        job_id=uuid.uuid4(), current_phase=Phase.PREFLIGHT,
        next_phase=Phase.PROVISIONING, lease_holder="me",
    )
    assert ok is False


@pytest.mark.asyncio
async def test_fail_returns_bool():
    conn = MagicMock()
    conn.execute = AsyncMock(return_value="UPDATE 1")
    repo = ProvisioningJobRepository(_make_db_with_conn(conn))
    err = ClassifiedError(ErrorClass.PERMANENT, "X", "m")
    ok = await repo.fail(
        job_id=uuid.uuid4(), current_phase=Phase.PREFLIGHT,
        lease_holder="me", error=err,
    )
    assert ok is True


@pytest.mark.asyncio
async def test_schedule_retry_returns_bool():
    conn = MagicMock()
    conn.execute = AsyncMock(return_value="UPDATE 1")
    repo = ProvisioningJobRepository(_make_db_with_conn(conn))
    err = ClassifiedError(ErrorClass.TRANSIENT, "X", "m")
    ok = await repo.schedule_retry(
        job_id=uuid.uuid4(), current_phase=Phase.PROVISIONING,
        lease_holder="me",
        next_attempt_after=datetime(2026, 5, 28, tzinfo=timezone.utc),
        attempt_count=1, error=err,
    )
    assert ok is True


@pytest.mark.asyncio
async def test_affected_one_does_not_match_multi_digit_counts():
    """Regression guard for 'UPDATE 11'.endswith(' 1') == True bug."""
    from inferia.services.orchestration.services.provisioning.jobs.repository import (
        _affected_one,
    )
    assert _affected_one("UPDATE 1") is True
    assert _affected_one("UPDATE 11") is False
    assert _affected_one("UPDATE 21") is False
    assert _affected_one("UPDATE 0") is False
    assert _affected_one("INSERT 0 1") is True
    assert _affected_one("INSERT 0 11") is False

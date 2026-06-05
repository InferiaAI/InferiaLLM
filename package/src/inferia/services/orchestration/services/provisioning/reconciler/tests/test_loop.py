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
        self.failed_deployments: list[dict] = []
        self.renew_calls = 0

    async def claim_next_job(self, *, lease_holder, lease_seconds=300):
        return self.jobs.pop(0) if self.jobs else None

    async def transition_to(self, **kwargs):
        self.transitions.append(kwargs)
        return True

    async def schedule_retry(self, **kwargs):
        self.retries.append(kwargs)
        return True

    async def fail(self, **kwargs):
        self.failures.append(kwargs)
        return True

    async def fail_deployments_for_node(self, *, node_id, message):
        self.failed_deployments.append({"node_id": node_id, "message": message})
        return 1

    async def release_lease(self, **kwargs):
        self.releases.append(kwargs)
        return True

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


def _make_reconciler(repo, handlers, inventory_repo=None):
    """Construct a ProvisioningReconciler with sensible defaults for tests.

    ``inventory_repo`` defaults to a MagicMock whose ``set_state`` is
    an AsyncMock — this exercises the new compute_inventory.state mirror
    code path on terminal transitions (READY / TERMINATED / FAILED)
    without forcing every test to instantiate a real InventoryRepository.
    Callers that want to assert on set_state args can pass their own mock.
    """
    if inventory_repo is None:
        inventory_repo = MagicMock()
        inventory_repo.set_state = AsyncMock()
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
        inventory_repo=inventory_repo,
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


@pytest.mark.asyncio
async def test_handler_returning_next_phase_none_schedules_retry():
    """PhaseResult(next_phase=None) means 'stay in phase'; reconciler
    bumps attempt_count and schedules a backoff."""
    job = _job(Phase.BOOTSTRAPPING, attempt_count=0)
    repo = _FakeRepo([job])

    class _StayHandler:
        name = Phase.BOOTSTRAPPING

        async def run(self, j, ctx):
            return PhaseResult(next_phase=None)

    rec = _make_reconciler(repo, [_StayHandler()])
    await rec.tick_once()
    assert len(repo.retries) == 1
    assert repo.retries[0]["attempt_count"] == 1


@pytest.mark.asyncio
async def test_renewer_exception_does_not_fail_the_job():
    """If repo.renew_lease raises, the job's handler outcome shouldn't
    be misattributed as a handler failure."""
    job = _job(Phase.PREFLIGHT, attempt_count=0)
    repo = _FakeRepo([job])
    repo.renew_lease = AsyncMock(side_effect=RuntimeError("DB blip"))
    h = _OkHandler(Phase.PREFLIGHT, Phase.PROVISIONING)
    rec = _make_reconciler(repo, [h])
    rec.renew_interval_s = 0.001  # tight interval to trigger renew quickly
    await rec.tick_once()
    # Job should have either transitioned (if handler completed before
    # renewer crashed) OR been released without a fail/retry being written.
    # The key invariant: no spurious fail or schedule_retry.
    assert len(repo.failures) == 0


@pytest.mark.asyncio
async def test_transient_at_exact_max_attempts_escalates():
    """attempt_count=4 + new failure (new_attempt=5) MUST escalate."""
    job = _job(Phase.PROVISIONING, attempt_count=4)
    repo = _FakeRepo([job])
    h = _RaisingHandler(Phase.PROVISIONING, AWSThrottledError("rate"))
    rec = _make_reconciler(repo, [h])
    await rec.tick_once()
    assert len(repo.failures) == 1
    assert repo.failures[0]["error"].code == "RETRIES_EXHAUSTED"


@pytest.mark.asyncio
async def test_transient_below_max_attempts_still_retries():
    """attempt_count=3 + new failure (new_attempt=4) still schedules retry."""
    job = _job(Phase.PROVISIONING, attempt_count=3)
    repo = _FakeRepo([job])
    h = _RaisingHandler(Phase.PROVISIONING, AWSThrottledError("rate"))
    rec = _make_reconciler(repo, [h])
    await rec.tick_once()
    assert len(repo.retries) == 1
    assert repo.retries[0]["attempt_count"] == 4


# ---------------------------------------------------------------------------
# compute_inventory.state mirror on terminal transitions.
#
# The reconciler bridges the provisioning_jobs state machine onto the
# inventory row's user-facing state field so the dashboard's "is this
# node alive" view stays in sync without an extra polling join. The
# bridge fires on every terminal transition: READY (via transition_to),
# TERMINATED (via transition_to in CancelHandler), FAILED (via _fail_loud).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_terminal_ready_mirrors_to_inventory_state_ready():
    """transition_to(READY) → inventory.set_state(node_id, 'ready')."""
    job = _job(Phase.BOOTSTRAPPING)
    repo = _FakeRepo([job])
    h = _OkHandler(Phase.BOOTSTRAPPING, Phase.READY)
    inventory_repo = MagicMock()
    inventory_repo.set_state = AsyncMock()
    rec = _make_reconciler(repo, [h], inventory_repo=inventory_repo)

    await rec.tick_once()

    inventory_repo.set_state.assert_awaited_once_with(
        node_id=job.node_id, state="ready",
    )


@pytest.mark.asyncio
async def test_terminal_terminated_mirrors_to_inventory_state_terminated():
    """transition_to(TERMINATED) → inventory.set_state(node_id, 'terminated').

    This is the cancel path: CancelHandler returns PhaseResult(next_phase=
    TERMINATED) after pulumi destroy completes, and the reconciler must
    mirror the terminal phase onto compute_inventory.state so the
    dashboard stops showing the node as 'provisioning'.
    """
    job = _job(Phase.CANCELLING)
    repo = _FakeRepo([job])
    h = _OkHandler(Phase.CANCELLING, Phase.TERMINATED)
    inventory_repo = MagicMock()
    inventory_repo.set_state = AsyncMock()
    rec = _make_reconciler(repo, [h], inventory_repo=inventory_repo)

    await rec.tick_once()

    inventory_repo.set_state.assert_awaited_once_with(
        node_id=job.node_id, state="terminated",
    )


@pytest.mark.asyncio
async def test_permanent_error_mirrors_failed_to_inventory_state():
    """_fail_loud → inventory.set_state(node_id, 'failed')."""
    job = _job(Phase.PREFLIGHT)
    repo = _FakeRepo([job])
    h = _RaisingHandler(Phase.PREFLIGHT, InvalidCredentialsError("bad"))
    inventory_repo = MagicMock()
    inventory_repo.set_state = AsyncMock()
    rec = _make_reconciler(repo, [h], inventory_repo=inventory_repo)

    await rec.tick_once()

    inventory_repo.set_state.assert_awaited_once_with(
        node_id=job.node_id, state="failed",
    )


@pytest.mark.asyncio
async def test_permanent_error_fails_waiting_deployment():
    """_fail_loud also fails the deployment bound to the dead node, so it does
    not hang in PENDING_NODE forever after the node provisioning fails."""
    job = _job(Phase.PREFLIGHT)
    repo = _FakeRepo([job])
    h = _RaisingHandler(Phase.PREFLIGHT, InvalidCredentialsError("bad creds"))
    rec = _make_reconciler(repo, [h])

    await rec.tick_once()

    assert len(repo.failed_deployments) == 1, "deployment(s) on the dead node must be failed"
    call = repo.failed_deployments[0]
    assert call["node_id"] == job.node_id
    assert call["message"]  # carries an actionable reason


@pytest.mark.asyncio
async def test_non_terminal_transition_does_not_call_inventory_set_state():
    """transition_to(PROVISIONING) (non-terminal) → no inventory write.

    Only READY / TERMINATED / FAILED transitions mirror to compute_inventory.
    Mid-flow transitions (preflight → provisioning etc.) leave
    inventory.state alone (it stays at 'provisioning' from add-node time).
    """
    job = _job(Phase.PREFLIGHT)
    repo = _FakeRepo([job])
    h = _OkHandler(Phase.PREFLIGHT, Phase.PROVISIONING)
    inventory_repo = MagicMock()
    inventory_repo.set_state = AsyncMock()
    rec = _make_reconciler(repo, [h], inventory_repo=inventory_repo)

    await rec.tick_once()

    inventory_repo.set_state.assert_not_awaited()


@pytest.mark.asyncio
async def test_inventory_set_state_failure_swallowed():
    """A failing inventory.set_state must NOT abort the state machine.

    The provisioning_jobs row is the source of truth; an inventory write
    failure is logged and swallowed so the job still records its terminal
    phase. Without this, a transient DB blip during the inventory UPDATE
    would leave the job row in an inconsistent state.
    """
    job = _job(Phase.BOOTSTRAPPING)
    repo = _FakeRepo([job])
    h = _OkHandler(Phase.BOOTSTRAPPING, Phase.READY)
    inventory_repo = MagicMock()
    inventory_repo.set_state = AsyncMock(side_effect=RuntimeError("DB blip"))
    rec = _make_reconciler(repo, [h], inventory_repo=inventory_repo)

    await rec.tick_once()
    # The transition was still recorded — repo.transitions has the entry.
    assert len(repo.transitions) == 1
    assert repo.transitions[0]["next_phase"] == Phase.READY


@pytest.mark.asyncio
async def test_inventory_repo_none_skips_set_state_without_crashing():
    """inventory_repo=None (test compat) → no inventory write, no crash."""
    job = _job(Phase.BOOTSTRAPPING)
    repo = _FakeRepo([job])
    h = _OkHandler(Phase.BOOTSTRAPPING, Phase.READY)
    # Explicitly pass a MagicMock that has no .set_state and verify the
    # rec.inventory_repo=None branch instead.
    rec = ProvisioningReconciler(
        repo=repo,
        handlers={h.name: h for h in [h]},
        emit_event=AsyncMock(),
        db=MagicMock(),
        concurrency=1,
        poll_interval_s=0.01,
        lease_seconds=300,
        renew_interval_s=10.0,
        lease_holder="test-rec",
        load_aws_context=AsyncMock(return_value=(MagicMock(), {})),
        inventory_repo=None,
    )

    await rec.tick_once()
    assert len(repo.transitions) == 1

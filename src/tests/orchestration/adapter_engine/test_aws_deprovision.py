"""Tests for the aws_deprovision helper (EC2 stack destroy + DB transitions)."""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Fakes.
# ---------------------------------------------------------------------------


class FakeConn:
    """Captures asyncpg-style execute calls and lets the test inspect SQL."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.closed = False

    async def execute(self, sql: str, *args) -> None:
        self.calls.append((sql, args))

    async def fetchrow(self, sql: str, *args):
        return None

    async def close(self) -> None:
        self.closed = True


class FakeAcquireCtx:
    def __init__(self, conn: FakeConn) -> None:
        self._conn = conn

    async def __aenter__(self) -> FakeConn:
        return self._conn

    async def __aexit__(self, *_exc) -> None:
        return None


class FakePool:
    """Minimal asyncpg pool that hands out FakeConn instances."""

    def __init__(self) -> None:
        self.conn = FakeConn()
        self.acquire_count = 0

    def acquire(self) -> FakeAcquireCtx:
        self.acquire_count += 1
        return FakeAcquireCtx(self.conn)


class FakeAdapter:
    def __init__(self, *, raises: BaseException | None = None) -> None:
        self.raises = raises
        self.calls: list[dict] = []
        self.db: Any = None

    async def deprovision_node(
        self, *, provider_instance_id: str, **_kwargs
    ) -> None:
        self.calls.append({"provider_instance_id": provider_instance_id})
        if self.raises is not None:
            raise self.raises


def _make_factory(*, raises: BaseException | None = None):
    """Return a callable matching ADAPTER_REGISTRY's class-factory shape."""
    last: dict[str, FakeAdapter] = {}

    def factory(*, db=None):
        adapter = FakeAdapter(raises=raises)
        adapter.db = db
        last["instance"] = adapter
        return adapter

    factory.last = last  # type: ignore[attr-defined]
    return factory


# ---------------------------------------------------------------------------
# Happy path.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_happy_path_transitions_terminated() -> None:
    from services.orchestration.adapter_engine import (
        aws_deprovision,
    )

    pool = FakePool()
    factory = _make_factory()

    from services.orchestration.adapter_engine import (
        aws_deprovision as _awsdep,
    )
    with patch.object(_awsdep, "ADAPTER_REGISTRY", {"aws": factory}):
        await aws_deprovision.deprovision_aws_node(
            pool_id="pool-1",
            node_id="node-1",
            db_pool=pool,
        )

    # Adapter was called with the pool_id as provider_instance_id.
    inst: FakeAdapter = factory.last["instance"]  # type: ignore[attr-defined]
    assert inst.calls == [{"provider_instance_id": "pool-1"}]
    # Adapter was built per-call with the acquired conn (race-safe pattern).
    assert inst.db is pool.conn
    # DB transitions: state='terminated'.
    sqls = [c[0] for c in pool.conn.calls]
    assert any("state = 'terminated'" in s for s in sqls)
    # The final state-update used node_id positional arg.
    for sql, args in pool.conn.calls:
        if "state = 'terminated'" in sql:
            assert "node-1" in args


# ---------------------------------------------------------------------------
# Failure path.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_failure_records_destroy_failed_with_reason() -> None:
    from services.orchestration.adapter_engine import (
        aws_deprovision,
    )

    pool = FakePool()
    factory = _make_factory(raises=RuntimeError("pulumi boom"))

    from services.orchestration.adapter_engine import (
        aws_deprovision as _awsdep,
    )
    with patch.object(_awsdep, "ADAPTER_REGISTRY", {"aws": factory}):
        await aws_deprovision.deprovision_aws_node(
            pool_id="pool-1",
            node_id="node-1",
            db_pool=pool,
        )

    sqls = [c[0] for c in pool.conn.calls]
    assert any("destroy_failed" in s for s in sqls)
    # The destroy_error reason is one of the parameters of the failure update.
    found_reason = False
    for sql, args in pool.conn.calls:
        if "destroy_failed" in sql:
            joined = " ".join(str(a) for a in args)
            if "pulumi boom" in joined:
                found_reason = True
                break
    assert found_reason, "destroy_error string should appear in args for the destroy_failed update"


# ---------------------------------------------------------------------------
# Cancel mid-flight: asyncio.shield must keep the DB transition happening.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_mid_flight_still_completes_state_machine() -> None:
    from services.orchestration.adapter_engine import (
        aws_deprovision,
    )

    pool = FakePool()

    # Adapter sleeps long enough that we can cancel its parent task.
    started = asyncio.Event()
    finished = asyncio.Event()

    class SlowAdapter:
        def __init__(self) -> None:
            self.db: Any = None

        async def deprovision_node(self, *, provider_instance_id: str, **_):
            started.set()
            await asyncio.sleep(0.05)  # short but long enough for the cancel
            finished.set()

    def factory(*, db=None):
        adapter = SlowAdapter()
        adapter.db = db
        return adapter

    from services.orchestration.adapter_engine import (
        aws_deprovision as _awsdep,
    )
    with patch.object(_awsdep, "ADAPTER_REGISTRY", {"aws": factory}):
        task = asyncio.create_task(
            aws_deprovision.deprovision_aws_node(
                pool_id="pool-1", node_id="node-1", db_pool=pool,
            )
        )
        await started.wait()
        task.cancel()
        # asyncio.shield protects the inner adapter call; the wrapper
        # raises CancelledError but the inner work proceeds. We give it
        # a moment to wrap up the DB transition.
        with pytest.raises(asyncio.CancelledError):
            await task
        # The inner task should still complete the destroy after the
        # cancellation point — shield holds it open. The wrapper that
        # awaits it transitions state after.
        await asyncio.wait_for(finished.wait(), timeout=1.0)
        # Give scheduler one tick for the wrapper's transition write.
        await asyncio.sleep(0.05)
    sqls = [c[0] for c in pool.conn.calls]
    assert any("state = 'terminated'" in s for s in sqls)


# ---------------------------------------------------------------------------
# Missing pool_id rejected cleanly (no DB mutation).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_missing_pool_id_is_no_op() -> None:
    from services.orchestration.adapter_engine import (
        aws_deprovision,
    )

    pool = FakePool()
    factory = _make_factory()
    from services.orchestration.adapter_engine import (
        aws_deprovision as _awsdep,
    )
    with patch.object(_awsdep, "ADAPTER_REGISTRY", {"aws": factory}):
        # Empty pool_id should short-circuit before adapter invocation.
        await aws_deprovision.deprovision_aws_node(
            pool_id="", node_id="node-1", db_pool=pool,
        )
    # No SQL execute calls fired.
    assert pool.conn.calls == []
    # No adapter ever instantiated.
    assert "instance" not in factory.last  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_missing_node_id_is_no_op() -> None:
    from services.orchestration.adapter_engine import (
        aws_deprovision,
    )

    pool = FakePool()
    factory = _make_factory()
    from services.orchestration.adapter_engine import (
        aws_deprovision as _awsdep,
    )
    with patch.object(_awsdep, "ADAPTER_REGISTRY", {"aws": factory}):
        await aws_deprovision.deprovision_aws_node(
            pool_id="pool-1", node_id="", db_pool=pool,
        )
    assert pool.conn.calls == []


# ---------------------------------------------------------------------------
# _spawn_destroy: background-task tracker prevents GC.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_spawn_destroy_tracks_task_until_done() -> None:
    from services.orchestration.adapter_engine import (
        aws_deprovision,
    )

    pool = FakePool()
    factory = _make_factory()
    aws_deprovision._BG.clear()  # ensure clean state
    from services.orchestration.adapter_engine import (
        aws_deprovision as _awsdep,
    )
    with patch.object(_awsdep, "ADAPTER_REGISTRY", {"aws": factory}):
        task = aws_deprovision._spawn_destroy(
            pool_id="pool-1", node_id="node-1", db_pool=pool,
        )
        assert task in aws_deprovision._BG
        await task
    # Done callback removed the task from the set.
    assert task not in aws_deprovision._BG


@pytest.mark.asyncio
async def test_spawn_destroy_callback_runs_on_error() -> None:
    """If the underlying deprovision raises, the task still self-discards."""
    from services.orchestration.adapter_engine import (
        aws_deprovision,
    )

    pool = FakePool()
    factory = _make_factory(raises=RuntimeError("boom"))
    aws_deprovision._BG.clear()
    from services.orchestration.adapter_engine import (
        aws_deprovision as _awsdep,
    )
    with patch.object(_awsdep, "ADAPTER_REGISTRY", {"aws": factory}):
        task = aws_deprovision._spawn_destroy(
            pool_id="pool-1", node_id="node-1", db_pool=pool,
        )
        # deprovision_aws_node swallows the adapter error and writes
        # destroy_failed itself, so awaiting the task should not raise.
        await task
    assert task not in aws_deprovision._BG

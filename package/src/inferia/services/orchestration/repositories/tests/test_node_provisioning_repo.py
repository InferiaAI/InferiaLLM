"""Tests for NodeProvisioningRepo. Uses an in-memory asyncpg fake.

The repo only needs `fetchval`, `fetch`, `fetchrow`, `execute`. Mock at
that level so the tests don't require a live postgres.
"""
from __future__ import annotations
import pytest
from uuid import uuid4
from datetime import datetime, timezone

from inferia.services.orchestration.repositories.node_provisioning_repo import (
    NodeProvisioningRepo,
    PHASES,
)


class FakeConn:
    def __init__(self):
        self.events = []  # rows: [{id, pool_id, node_id, phase, status, message, created_at}]
        self._next_id = 1

    async def fetchval(self, query, *args):
        # Only the INSERT ... RETURNING id path uses fetchval here.
        assert "INSERT INTO node_provisioning_events" in query
        row = {
            "id": self._next_id,
            "pool_id": args[0],
            "node_id": args[1],
            "phase": args[2],
            "status": args[3],
            "message": args[4],
            "created_at": datetime.now(timezone.utc),
        }
        self._next_id += 1
        self.events.append(row)
        return row["id"]

    async def fetch(self, query, *args):
        # list_events_after: SELECT ... WHERE pool_id=$1 AND id>$2 ORDER BY id LIMIT $3
        if "ORDER BY id" in query:
            pool_id, after_id, limit = args
            return [
                dict(r) for r in self.events
                if r["pool_id"] == pool_id and r["id"] > after_id
            ][:limit]
        # summarize_phases: SELECT DISTINCT ON (phase) ...
        if "DISTINCT ON" in query:
            (pool_id,) = args
            latest = {}
            firsts = {}
            for r in self.events:
                if r["pool_id"] != pool_id:
                    continue
                if r["status"] == "log":
                    continue
                latest[r["phase"]] = r
                firsts.setdefault(r["phase"], r)
            out = []
            for phase, r in latest.items():
                out.append({
                    "phase": phase,
                    "status": r["status"],
                    "started_at": firsts[phase]["created_at"],
                    "ended_at": r["created_at"] if r["status"] in ("succeeded", "failed") else None,
                    "last_message": r["message"],
                })
            return out
        raise AssertionError(f"unexpected query: {query}")


@pytest.fixture
def repo():
    return NodeProvisioningRepo(FakeConn())


@pytest.mark.asyncio
async def test_append_event_returns_monotonic_id(repo):
    pool = uuid4()
    a = await repo.append_event(pool_id=pool, phase="prepare", status="running")
    b = await repo.append_event(pool_id=pool, phase="prepare", status="succeeded")
    assert a == 1
    assert b == 2


@pytest.mark.asyncio
async def test_append_event_persists_optional_fields(repo):
    pool = uuid4()
    node = uuid4()
    await repo.append_event(
        pool_id=pool, phase="prepare", status="running",
        message="loading creds", node_id=node,
    )
    assert repo.db.events[0]["message"] == "loading creds"
    assert repo.db.events[0]["node_id"] == node


@pytest.mark.asyncio
async def test_list_events_after_cursor_advances(repo):
    pool = uuid4()
    for i in range(5):
        await repo.append_event(pool_id=pool, phase="pulumi_up", status="log",
                                message=f"line {i}")
    page1 = await repo.list_events_after(pool_id=pool, after_id=0, limit=3)
    assert len(page1) == 3
    assert [r["id"] for r in page1] == [1, 2, 3]
    last = page1[-1]["id"]
    page2 = await repo.list_events_after(pool_id=pool, after_id=last, limit=10)
    assert [r["id"] for r in page2] == [4, 5]


@pytest.mark.asyncio
async def test_list_events_after_empty_pool_returns_empty(repo):
    out = await repo.list_events_after(pool_id=uuid4(), after_id=0, limit=10)
    assert out == []


@pytest.mark.asyncio
async def test_list_events_after_limit_enforced(repo):
    pool = uuid4()
    for i in range(10):
        await repo.append_event(pool_id=pool, phase="cloud_init", status="log")
    out = await repo.list_events_after(pool_id=pool, after_id=0, limit=4)
    assert len(out) == 4


@pytest.mark.asyncio
async def test_summarize_phases_returns_latest_per_phase(repo):
    pool = uuid4()
    await repo.append_event(pool_id=pool, phase="prepare", status="running")
    await repo.append_event(pool_id=pool, phase="prepare", status="succeeded")
    await repo.append_event(pool_id=pool, phase="ami_lookup", status="running")
    summary = await repo.summarize_phases(pool_id=pool)
    by_phase = {r["phase"]: r for r in summary}
    assert by_phase["prepare"]["status"] == "succeeded"
    assert by_phase["prepare"]["ended_at"] is not None
    assert by_phase["ami_lookup"]["status"] == "running"
    assert by_phase["ami_lookup"]["ended_at"] is None


@pytest.mark.asyncio
async def test_summarize_phases_ignores_log_events(repo):
    pool = uuid4()
    await repo.append_event(pool_id=pool, phase="pulumi_up", status="running")
    for i in range(20):
        await repo.append_event(pool_id=pool, phase="pulumi_up", status="log",
                                message=f"l{i}")
    await repo.append_event(pool_id=pool, phase="pulumi_up", status="succeeded")
    summary = await repo.summarize_phases(pool_id=pool)
    assert len(summary) == 1
    assert summary[0]["status"] == "succeeded"


@pytest.mark.asyncio
async def test_summarize_phases_unknown_pool_returns_empty(repo):
    out = await repo.summarize_phases(pool_id=uuid4())
    assert out == []


@pytest.mark.asyncio
async def test_current_phase_returns_running_phase(repo):
    pool = uuid4()
    await repo.append_event(pool_id=pool, phase="prepare", status="succeeded")
    await repo.append_event(pool_id=pool, phase="pulumi_up", status="running")
    assert await repo.current_phase(pool_id=pool) == "pulumi_up"


@pytest.mark.asyncio
async def test_current_phase_returns_none_when_terminal(repo):
    pool = uuid4()
    await repo.append_event(pool_id=pool, phase="ready", status="succeeded")
    assert await repo.current_phase(pool_id=pool) is None


@pytest.mark.asyncio
async def test_phases_constant_has_eight_entries():
    assert PHASES == (
        "prepare", "ami_lookup", "pulumi_init", "pulumi_up",
        "ec2_running", "cloud_init", "worker_bootstrap", "ready",
    )

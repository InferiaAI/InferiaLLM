"""Tests for the node-centric repo methods on InventoryRepository +
ComputePoolRepository.ensure_default_pool."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from inferia.services.orchestration.repositories.inventory_repo import (
    InventoryRepository,
)
from inferia.services.orchestration.repositories.pool_repo import (
    ComputePoolRepository,
)


class _AsyncCtx:
    def __init__(self, value):
        self._value = value

    async def __aenter__(self):
        return self._value

    async def __aexit__(self, *a):
        return False


def make_db(fetchrow=None, fetchval=None, fetch=None):
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value=fetchrow)
    conn.fetchval = AsyncMock(return_value=fetchval)
    conn.fetch = AsyncMock(return_value=fetch or [])
    conn.execute = AsyncMock(return_value="UPDATE 1")
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=_AsyncCtx(conn))
    return pool, conn


ORG = "69ff5234-a4ea-4c88-adf0-d5702508f7ef"
POOL = "942d7675-5633-4a72-a5e7-defbf4866ab5"
NODE = "11111111-2222-3333-4444-555555555555"


# ---------------------------------------------------------------------------
# pool_repo.ensure_default_pool
# ---------------------------------------------------------------------------


class TestEnsureDefaultPool:
    @pytest.mark.asyncio
    async def test_returns_existing(self):
        pool, _ = make_db(fetchval=POOL)
        repo = ComputePoolRepository(pool)
        got = await repo.ensure_default_pool(org_id=ORG)
        assert got == POOL

    @pytest.mark.asyncio
    async def test_creates_when_absent(self):
        pool, conn = make_db()
        # First fetchval (SELECT) → None; second (INSERT ... RETURNING id) → new id.
        conn.fetchval = AsyncMock(side_effect=[None, "new-pool-uuid"])
        repo = ComputePoolRepository(pool)
        got = await repo.ensure_default_pool(org_id=ORG)
        assert got == "new-pool-uuid"
        assert conn.fetchval.await_count == 2

    @pytest.mark.asyncio
    async def test_concurrent_callers_converge(self):
        # Two callers see the same persisted row (ON CONFLICT DO NOTHING
        # / coalescing semantics on the SQL side); the test fake just
        # returns the same id to both.
        pool, conn = make_db(fetchval=POOL)
        repo = ComputePoolRepository(pool)
        a = await repo.ensure_default_pool(org_id=ORG)
        b = await repo.ensure_default_pool(org_id=ORG)
        assert a == b == POOL


# ---------------------------------------------------------------------------
# inventory_repo.list_nodes
# ---------------------------------------------------------------------------


class TestListNodes:
    @pytest.mark.asyncio
    async def test_empty_org(self):
        pool, _ = make_db(fetch=[])
        repo = InventoryRepository(pool)
        rows = await repo.list_nodes(org_id=ORG)
        assert rows == []

    @pytest.mark.asyncio
    async def test_no_selector_excludes_terminated(self):
        pool, conn = make_db(fetch=[{"id": "a", "state": "ready"}])
        repo = InventoryRepository(pool)
        rows = await repo.list_nodes(org_id=ORG)
        assert len(rows) == 1
        sql = conn.fetch.await_args.args[0]
        assert "terminated" in sql.lower()

    @pytest.mark.asyncio
    async def test_with_selector(self):
        pool, conn = make_db(fetch=[{"id": "a", "state": "ready", "labels": {"gpu": "h100"}}])
        repo = InventoryRepository(pool)
        rows = await repo.list_nodes(org_id=ORG, selector={"gpu": "h100"})
        assert len(rows) == 1
        # Selector should be passed as JSONB and tested with the @> operator.
        sql = conn.fetch.await_args.args[0]
        assert "@>" in sql
        # The selector dict serialised as the bind param.
        passed = conn.fetch.await_args.args[1:]
        assert any('"gpu"' in (a if isinstance(a, str) else str(a)) for a in passed)

    @pytest.mark.asyncio
    async def test_selector_with_dotted_keys(self):
        pool, _ = make_db(fetch=[])
        repo = InventoryRepository(pool)
        await repo.list_nodes(org_id=ORG, selector={"inferia.io/zone": "eu-west"})
        # Just verify no exception; the value goes through as plain JSON.


# ---------------------------------------------------------------------------
# inventory_repo.set_labels
# ---------------------------------------------------------------------------


class TestSetLabels:
    @pytest.mark.asyncio
    async def test_add_merges_existing(self):
        pool, conn = make_db(fetchrow={"id": NODE, "labels": {"old": "v"}})
        # After the update, return the merged labels.
        conn.fetchrow = AsyncMock(side_effect=[
            {"id": NODE, "labels": {"old": "v"}, "state": "ready"},
            {"id": NODE, "labels": {"old": "v", "env": "prod"}, "state": "ready"},
        ])
        repo = InventoryRepository(pool)
        row = await repo.set_labels(node_id=NODE, add={"env": "prod"}, remove=[])
        assert row["labels"] == {"old": "v", "env": "prod"}

    @pytest.mark.asyncio
    async def test_remove_unsets(self):
        pool, conn = make_db()
        conn.fetchrow = AsyncMock(side_effect=[
            {"id": NODE, "labels": {"env": "prod", "extra": "x"}, "state": "ready"},
            {"id": NODE, "labels": {"env": "prod"}, "state": "ready"},
        ])
        repo = InventoryRepository(pool)
        row = await repo.set_labels(node_id=NODE, add={}, remove=["extra"])
        assert "extra" not in row["labels"]

    @pytest.mark.asyncio
    async def test_node_not_found(self):
        from inferia.services.orchestration.repositories.inventory_repo import (
            NodeNotFoundError,
        )
        pool, conn = make_db()
        conn.fetchrow = AsyncMock(return_value=None)
        repo = InventoryRepository(pool)
        with pytest.raises(NodeNotFoundError):
            await repo.set_labels(node_id=NODE, add={"env": "prod"}, remove=[])

    @pytest.mark.asyncio
    async def test_terminated_node_rejected(self):
        from inferia.services.orchestration.repositories.inventory_repo import (
            NodeTerminatedError,
        )
        pool, conn = make_db()
        conn.fetchrow = AsyncMock(return_value={
            "id": NODE, "labels": {}, "state": "terminated",
        })
        repo = InventoryRepository(pool)
        with pytest.raises(NodeTerminatedError):
            await repo.set_labels(node_id=NODE, add={"env": "prod"}, remove=[])

    @pytest.mark.asyncio
    async def test_overlapping_add_remove_rejected(self):
        from inferia.services.orchestration.repositories.inventory_repo import (
            LabelConflictError,
        )
        pool, _ = make_db()
        repo = InventoryRepository(pool)
        with pytest.raises(LabelConflictError):
            await repo.set_labels(
                node_id=NODE, add={"env": "prod"}, remove=["env"],
            )


# ---------------------------------------------------------------------------
# inventory_repo.soft_delete_node
# ---------------------------------------------------------------------------


class TestSoftDeleteNode:
    @pytest.mark.asyncio
    async def test_marks_terminated(self):
        pool, conn = make_db()
        repo = InventoryRepository(pool)
        await repo.soft_delete_node(node_id=NODE)
        sql = conn.execute.await_args.args[0]
        assert "terminated" in sql.lower()

    @pytest.mark.asyncio
    async def test_idempotent(self):
        pool, conn = make_db()
        repo = InventoryRepository(pool)
        await repo.soft_delete_node(node_id=NODE)
        await repo.soft_delete_node(node_id=NODE)
        assert conn.execute.await_count == 2  # both run, both idempotent at SQL level


# ---------------------------------------------------------------------------
# inventory_repo.get_node
# ---------------------------------------------------------------------------


class TestGetNode:
    @pytest.mark.asyncio
    async def test_returns_row(self):
        pool, _ = make_db(fetchrow={"id": NODE, "state": "ready", "labels": {}})
        repo = InventoryRepository(pool)
        row = await repo.get_node(node_id=NODE)
        assert row["id"] == NODE

    @pytest.mark.asyncio
    async def test_missing_returns_none(self):
        pool, _ = make_db(fetchrow=None)
        repo = InventoryRepository(pool)
        row = await repo.get_node(node_id=NODE)
        assert row is None

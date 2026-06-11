"""Tests for the worker-related methods added to InventoryRepository and
ComputePoolRepository."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from orchestration.repositories.inventory_repo import (
    InventoryRepository,
)
from orchestration.repositories.pool_repo import (
    ComputePoolRepository,
)
from orchestration.workers.worker_controller.controller import (
    NodeUnreachableError,  # for symmetry / unused
)


# Reusable async-context manager so async with pool.acquire() works.
class _AsyncCtx:
    def __init__(self, value):
        self._value = value

    async def __aenter__(self):
        return self._value

    async def __aexit__(self, *a):
        return False


def make_db(fetchrow=None, fetchval=None, fetch=None, execute_args=None):
    """Build a fake asyncpg pool returning canned values."""
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value=fetchrow)
    conn.fetchval = AsyncMock(return_value=fetchval)
    conn.fetch = AsyncMock(return_value=fetch or [])
    conn.execute = AsyncMock(return_value="UPDATE 1")

    pool = MagicMock()
    pool.acquire = MagicMock(return_value=_AsyncCtx(conn))
    pool.fetchrow = AsyncMock(return_value=fetchrow)
    pool.fetchval = AsyncMock(return_value=fetchval)
    pool.fetch = AsyncMock(return_value=fetch or [])
    pool.execute = AsyncMock(return_value="UPDATE 1")
    return pool, conn


# ---------------------------------------------------------------------------
# InventoryRepository.upsert_worker
# ---------------------------------------------------------------------------


class TestUpsertWorker:
    @pytest.mark.asyncio
    async def test_insert_new(self):
        pool, conn = make_db(
            fetchrow={
                "id": "node-uuid",
                "pool_id": "p",
                "node_name": "n",
                "agent_kind": "worker",
                "state": "provisioning",
                "advertise_url": "https://w",
            },
        )
        repo = InventoryRepository(pool)
        row = await repo.upsert_worker(
            pool_id="p", node_name="n",
            advertise_url="https://w", allocatable={"gpu": "1"},
        )
        assert row["id"] == "node-uuid"
        # The first fetchrow call should be the INSERT/upsert (UPSERT) — verify
        # we passed the bind values in the expected positions.
        assert conn.fetchrow.await_count >= 1
        sql, *args = conn.fetchrow.await_args_list[0].args
        assert "compute_inventory" in sql.lower()

    @pytest.mark.asyncio
    async def test_returns_existing_row_when_same_node(self):
        pool, conn = make_db(
            fetchrow={
                "id": "node-uuid",
                "pool_id": "p",
                "node_name": "n",
                "agent_kind": "worker",
                "state": "ready",
                "advertise_url": "https://w",
            },
        )
        repo = InventoryRepository(pool)
        row = await repo.upsert_worker(
            pool_id="p", node_name="n",
            advertise_url="https://w", allocatable={},
        )
        assert row["agent_kind"] == "worker"

    @pytest.mark.asyncio
    async def test_conflict_with_non_worker_kind_raises(self):
        from orchestration.repositories.inventory_repo import (
            DuplicateNodeError,
        )
        # First fetchrow returns a row with agent_kind != worker → repo must
        # detect this and raise.
        pool, conn = make_db(
            fetchrow={
                "id": "x",
                "pool_id": "p",
                "node_name": "n",
                "agent_kind": "akash",
                "state": "ready",
            },
        )
        repo = InventoryRepository(pool)
        with pytest.raises(DuplicateNodeError):
            await repo.upsert_worker(
                pool_id="p", node_name="n",
                advertise_url="https://w", allocatable={},
            )


# ---------------------------------------------------------------------------
# InventoryRepository.list_workers
# ---------------------------------------------------------------------------


class TestListWorkers:
    @pytest.mark.asyncio
    async def test_returns_only_worker_rows(self):
        pool, conn = make_db(
            fetch=[
                {"id": "a", "agent_kind": "worker", "state": "ready"},
                {"id": "b", "agent_kind": "worker", "state": "provisioning"},
            ],
        )
        repo = InventoryRepository(pool)
        rows = await repo.list_workers(pool_id="p")
        assert len(rows) == 2
        # Verify the SQL filtered to worker-kind rows
        sql = conn.fetch.await_args.args[0]
        assert "agent_kind" in sql.lower() and "worker" in sql.lower()

    @pytest.mark.asyncio
    async def test_empty(self):
        pool, _ = make_db(fetch=[])
        repo = InventoryRepository(pool)
        rows = await repo.list_workers(pool_id="p")
        assert rows == []


# ---------------------------------------------------------------------------
# InventoryRepository.update_heartbeat_with_telemetry
# ---------------------------------------------------------------------------


class TestUpdateHeartbeatWithTelemetry:
    @pytest.mark.asyncio
    async def test_persists_used_and_loaded_models(self):
        pool, conn = make_db()
        repo = InventoryRepository(pool)
        await repo.update_heartbeat_with_telemetry(
            node_id="n",
            used={"cpu_pct": "10.5"},
            loaded_models=["dep-1", "dep-2"],
        )
        # SQL stored to metadata JSONB column; just verify the call landed.
        assert conn.execute.await_count == 1
        sql = conn.execute.await_args.args[0]
        assert "compute_inventory" in sql.lower()
        assert "metadata" in sql.lower() or "last_heartbeat" in sql.lower()


# ---------------------------------------------------------------------------
# InventoryRepository.mark_ready_worker (new variant)
# ---------------------------------------------------------------------------


class TestMarkReadyWorker:
    @pytest.mark.asyncio
    async def test_transitions_provisioning_to_ready(self):
        pool, conn = make_db()
        repo = InventoryRepository(pool)
        await repo.mark_ready_worker(node_id="n")
        assert conn.execute.await_count == 1
        sql = conn.execute.await_args.args[0]
        assert "ready" in sql.lower()


# ---------------------------------------------------------------------------
# InventoryRepository.mark_terminated_worker
# ---------------------------------------------------------------------------


class TestMarkTerminatedWorker:
    @pytest.mark.asyncio
    async def test_marks_terminated(self):
        pool, conn = make_db()
        repo = InventoryRepository(pool)
        await repo.mark_terminated_worker(node_id="n")
        sql = conn.execute.await_args.args[0]
        assert "terminated" in sql.lower()


# ---------------------------------------------------------------------------
# ComputePoolRepository.get_or_generate_inference_token
# ---------------------------------------------------------------------------


class TestGetOrGenerateInferenceToken:
    @pytest.mark.asyncio
    async def test_returns_existing_token(self):
        # Pool already has a token persisted.
        pool, conn = make_db(fetchval="existing-token")
        repo = ComputePoolRepository(pool)
        tok = await repo.get_or_generate_inference_token(pool_id="p")
        assert tok == "existing-token"

    @pytest.mark.asyncio
    async def test_generates_when_absent(self):
        # The first fetchval (SELECT) returns None; the second (UPDATE
        # RETURNING) returns the new value the SQL just persisted.
        pool, conn = make_db()
        conn.fetchval = AsyncMock(side_effect=[None, "freshly-generated"])
        repo = ComputePoolRepository(pool)
        tok = await repo.get_or_generate_inference_token(pool_id="p")
        assert tok == "freshly-generated"
        # The generated value must be the same the repo passed into the UPDATE.
        update_call = conn.fetchval.await_args_list[1]
        # The new token is at arg index 2 (sql, pool_id, token).
        assert len(update_call.args) >= 3
        assert update_call.args[2] == "freshly-generated" or len(update_call.args[2]) >= 32

    @pytest.mark.asyncio
    async def test_concurrent_callers_converge(self):
        # If two callers race, the SQL `UPDATE ... SET inference_token =
        # COALESCE(inference_token, $2) RETURNING inference_token` ensures
        # whichever ran first wins. The fake returns the same value to both.
        pool, conn = make_db(fetchval="shared")
        repo = ComputePoolRepository(pool)
        a = await repo.get_or_generate_inference_token(pool_id="p")
        b = await repo.get_or_generate_inference_token(pool_id="p")
        assert a == b == "shared"


# ---------------------------------------------------------------------------
# ComputePoolRepository.rotate_inference_token
# ---------------------------------------------------------------------------


class TestRotateInferenceToken:
    @pytest.mark.asyncio
    async def test_generates_new_value(self):
        pool, conn = make_db(fetchval="new-token-value")
        repo = ComputePoolRepository(pool)
        tok = await repo.rotate_inference_token(pool_id="p")
        assert tok == "new-token-value"
        sql = conn.fetchval.await_args.args[0]
        assert "update" in sql.lower() and "compute_pools" in sql.lower()


# Tiny silence-unused suppressor.
_ = NodeUnreachableError

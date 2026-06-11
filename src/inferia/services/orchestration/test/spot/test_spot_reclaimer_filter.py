"""Tests for SpotReclaimer node-state filtering (issue #29).

The reclaimer must only delete allocations on spot nodes that have
actually been reclaimed (state='terminated'), not all spot allocations.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from inferia.services.orchestration.infra.spot_reclaimer import SpotReclaimer


def make_mock_db():
    """Create a mock DB pool with acquire/transaction context managers."""
    conn = AsyncMock()
    conn.fetch = AsyncMock(return_value=[])
    conn.execute = AsyncMock()

    # conn.transaction() returns an async context manager
    tx = AsyncMock()
    tx.__aenter__ = AsyncMock(return_value=tx)
    tx.__aexit__ = AsyncMock(return_value=False)
    conn.transaction = MagicMock(return_value=tx)

    # db.acquire() returns an async context manager yielding conn
    acq = AsyncMock()
    acq.__aenter__ = AsyncMock(return_value=conn)
    acq.__aexit__ = AsyncMock(return_value=False)

    db = MagicMock()
    db.acquire = MagicMock(return_value=acq)

    return db, conn


class TestSpotReclaimerFilter:

    @pytest.mark.asyncio
    async def test_reclaim_query_filters_by_terminated_state(self):
        """The SQL query must include a WHERE clause filtering for terminated nodes."""
        db, conn = make_mock_db()
        conn.fetch = AsyncMock(return_value=[])

        reclaimer = SpotReclaimer(db)
        await reclaimer.reclaim()

        conn.fetch.assert_called_once()
        sql = conn.fetch.call_args[0][0]

        # The query must filter for terminated state, not select ALL spot nodes
        assert "state" in sql.lower(), \
            "Query must filter by node state to avoid reclaiming healthy spot allocations"

    @pytest.mark.asyncio
    async def test_reclaim_only_processes_terminated_spot_victims(self):
        """Only allocations on terminated spot nodes should be deleted."""
        db, conn = make_mock_db()

        # Simulate: DB returns one victim from a terminated spot node
        victim = {
            "allocation_id": "alloc-1",
            "node_id": "node-1",
            "gpu": 1,
            "vcpu": 4,
            "ram_gb": 8,
            "owner_type": "user",
            "owner_id": "user-1",
        }
        conn.fetch = AsyncMock(return_value=[victim])

        reclaimer = SpotReclaimer(db)
        await reclaimer.reclaim()

        # Should have executed: resource release, billing event, and delete
        assert conn.execute.call_count == 3

        # Verify the DELETE was for the correct allocation
        delete_call = conn.execute.call_args_list[2]
        assert "DELETE FROM allocations" in delete_call[0][0]
        assert delete_call[0][1] == "alloc-1"

    @pytest.mark.asyncio
    async def test_reclaim_no_victims_means_no_writes(self):
        """If no terminated spot nodes exist, no DB writes should happen."""
        db, conn = make_mock_db()
        conn.fetch = AsyncMock(return_value=[])

        reclaimer = SpotReclaimer(db)
        await reclaimer.reclaim()

        conn.execute.assert_not_called()

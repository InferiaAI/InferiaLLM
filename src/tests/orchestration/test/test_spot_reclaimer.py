"""Tests for SpotReclaimer batch + SKIP LOCKED fix (#77)."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from services.orchestration.infra.spot_reclaimer import SpotReclaimer, BATCH_SIZE


def _make_victim(alloc_id="a1", node_id="n1", gpu=1, vcpu=4, ram_gb=16):
    return {
        "allocation_id": alloc_id,
        "node_id": node_id,
        "gpu": gpu,
        "vcpu": vcpu,
        "ram_gb": ram_gb,
        "owner_type": "user",
        "owner_id": "u1",
    }


class _BatchTracker:
    """Tracks batch calls across multiple connections."""
    def __init__(self, victims_per_batch):
        self.victims_per_batch = victims_per_batch
        self.call_count = 0

    async def fetch(self, query, *args):
        if self.call_count < len(self.victims_per_batch):
            result = self.victims_per_batch[self.call_count]
            self.call_count += 1
            return result
        return []


def _mock_db(victims_per_batch):
    """Create a mock DB pool that returns victims in batches."""
    db = MagicMock()
    tracker = _BatchTracker(victims_per_batch)

    conn = AsyncMock()
    txn = AsyncMock()
    txn.__aenter__ = AsyncMock(return_value=txn)
    txn.__aexit__ = AsyncMock(return_value=None)
    conn.transaction = MagicMock(return_value=txn)

    conn.fetch = tracker.fetch
    conn.execute = AsyncMock()
    conn.__aenter__ = AsyncMock(return_value=conn)
    conn.__aexit__ = AsyncMock(return_value=None)

    db.acquire = MagicMock(return_value=conn)
    return db, conn


class TestSpotReclaimer:
    def test_batch_size_is_reasonable(self):
        """BATCH_SIZE should be > 0 and <= 1000."""
        assert 0 < BATCH_SIZE <= 1000

    @pytest.mark.asyncio
    async def test_no_victims_returns_zero(self):
        db, conn = _mock_db([[]])
        reclaimer = SpotReclaimer(db)
        result = await reclaimer.reclaim()
        assert result == 0

    @pytest.mark.asyncio
    async def test_single_batch_processes_all(self):
        victims = [_make_victim(f"a{i}", f"n{i}") for i in range(3)]
        db, conn = _mock_db([victims, []])
        reclaimer = SpotReclaimer(db)

        result = await reclaimer.reclaim()
        assert result == 3

    @pytest.mark.asyncio
    async def test_multiple_batches(self):
        """When first batch is full, a second batch should be fetched."""
        batch1 = [_make_victim(f"a{i}", f"n{i}") for i in range(BATCH_SIZE)]
        batch2 = [_make_victim(f"a{BATCH_SIZE + i}", f"n{BATCH_SIZE + i}") for i in range(5)]
        db, conn = _mock_db([batch1, batch2, []])
        reclaimer = SpotReclaimer(db)

        result = await reclaimer.reclaim()
        assert result == BATCH_SIZE + 5

    @pytest.mark.asyncio
    async def test_each_victim_gets_three_operations(self):
        """Each victim should trigger: UPDATE inventory, INSERT billing, DELETE allocation."""
        victims = [_make_victim()]
        db, conn = _mock_db([victims, []])
        reclaimer = SpotReclaimer(db)

        await reclaimer.reclaim()
        # 3 execute calls per victim
        assert conn.execute.await_count == 3

    @pytest.mark.asyncio
    async def test_query_contains_skip_locked(self):
        """The SELECT query must include FOR UPDATE SKIP LOCKED."""
        db, conn = _mock_db([[]])
        reclaimer = SpotReclaimer(db)

        original_fetch = conn.fetch
        captured_queries = []

        async def capture_fetch(query, *args):
            captured_queries.append(query)
            return []

        conn.fetch = capture_fetch
        await reclaimer.reclaim()

        assert len(captured_queries) >= 1
        assert "SKIP LOCKED" in captured_queries[0]

    @pytest.mark.asyncio
    async def test_query_contains_limit(self):
        """The SELECT query must include LIMIT."""
        db, conn = _mock_db([[]])
        reclaimer = SpotReclaimer(db)

        captured_queries = []

        async def capture_fetch(query, *args):
            captured_queries.append(query)
            return []

        conn.fetch = capture_fetch
        await reclaimer.reclaim()

        assert len(captured_queries) >= 1
        assert "LIMIT" in captured_queries[0]

    @pytest.mark.asyncio
    async def test_stops_when_batch_smaller_than_batch_size(self):
        """Reclaim should stop when a batch returns fewer than BATCH_SIZE rows."""
        # Only 1 victim — less than BATCH_SIZE, so loop stops after 1 iteration
        batch1 = [_make_victim("a1", "n1")]
        db, conn = _mock_db([batch1])
        reclaimer = SpotReclaimer(db)

        result = await reclaimer.reclaim()
        assert result == 1

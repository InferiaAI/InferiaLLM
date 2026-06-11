"""
Tests for OutboxRepository transactional guarantees.

Verifies that fetch_pending runs inside an explicit transaction so that
FOR UPDATE SKIP LOCKED locks are held until the transaction commits,
preventing double-publish by concurrent workers.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from services.orchestration.repositories.outbox_repo import OutboxRepository


class _AsyncCtx:
    """Minimal async context manager wrapper for mocking."""

    def __init__(self, value):
        self._value = value
        self.entered = False
        self.exited = False

    async def __aenter__(self):
        self.entered = True
        return self._value

    async def __aexit__(self, *exc):
        self.exited = True
        return False


def _make_mock_pool():
    """
    Build an asyncpg-Pool-like mock that tracks acquire/transaction calls.

    Structure mirrors:
        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.fetch(...)
    """
    mock_conn = AsyncMock()
    mock_conn.fetch = AsyncMock(return_value=[])

    tx_ctx = _AsyncCtx(None)
    mock_conn.transaction = MagicMock(return_value=tx_ctx)

    acquire_ctx = _AsyncCtx(mock_conn)

    pool = MagicMock()
    pool.acquire = MagicMock(return_value=acquire_ctx)
    pool.fetch = AsyncMock(return_value=[])

    return pool, mock_conn, tx_ctx


class TestOutboxFetchPendingTransaction:
    """Verify fetch_pending runs inside a transaction."""

    @pytest.mark.asyncio
    async def test_fetch_pending_acquires_connection_and_opens_transaction(self):
        """fetch_pending must acquire a connection and start a transaction."""
        pool, mock_conn, tx_ctx = _make_mock_pool()
        repo = OutboxRepository(pool)

        await repo.fetch_pending(limit=10)

        pool.acquire.assert_called_once()
        mock_conn.transaction.assert_called_once()

    @pytest.mark.asyncio
    async def test_fetch_pending_uses_for_update_skip_locked(self):
        """The SELECT must still contain FOR UPDATE SKIP LOCKED."""
        pool, mock_conn, tx_ctx = _make_mock_pool()
        repo = OutboxRepository(pool)

        await repo.fetch_pending(limit=5)

        mock_conn.fetch.assert_called_once()
        sql = mock_conn.fetch.call_args[0][0]
        assert "FOR UPDATE SKIP LOCKED" in sql

    @pytest.mark.asyncio
    async def test_fetch_pending_queries_on_connection_not_pool(self):
        """The query must run on the acquired connection, not the pool."""
        pool, mock_conn, tx_ctx = _make_mock_pool()
        repo = OutboxRepository(pool)

        await repo.fetch_pending(limit=50)

        # The connection's fetch was used, not the pool's
        mock_conn.fetch.assert_called_once()
        pool.fetch.assert_not_called()

    @pytest.mark.asyncio
    async def test_fetch_pending_passes_limit(self):
        """The limit parameter must be forwarded to the SQL query."""
        pool, mock_conn, tx_ctx = _make_mock_pool()
        repo = OutboxRepository(pool)

        await repo.fetch_pending(limit=42)

        args = mock_conn.fetch.call_args[0]
        assert 42 in args

    @pytest.mark.asyncio
    async def test_fetch_pending_returns_dicts(self):
        """Rows must be returned as a list of dicts."""
        pool, mock_conn, tx_ctx = _make_mock_pool()

        fake_row = {"id": 1, "event_type": "test"}
        mock_conn.fetch = AsyncMock(return_value=[fake_row])
        repo = OutboxRepository(pool)

        result = await repo.fetch_pending(limit=10)

        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]["id"] == 1


class TestFetchAndLock:
    """Verify fetch_and_lock keeps the transaction open through mark_published."""

    @pytest.mark.asyncio
    async def test_fetch_and_lock_yields_connection(self):
        """fetch_and_lock should yield (events, conn) inside a transaction."""
        pool, mock_conn, tx_ctx = _make_mock_pool()
        repo = OutboxRepository(pool)

        async with repo.fetch_and_lock(limit=10) as (events, conn):
            assert conn is mock_conn

    @pytest.mark.asyncio
    async def test_transaction_open_inside_context(self):
        """Transaction must remain open while inside the context manager."""
        pool, mock_conn, tx_ctx = _make_mock_pool()
        repo = OutboxRepository(pool)

        async with repo.fetch_and_lock(limit=10) as (events, conn):
            # tx_ctx entered but not exited yet
            assert tx_ctx.entered
            assert not tx_ctx.exited

        # Now it should have exited
        assert tx_ctx.exited

    @pytest.mark.asyncio
    async def test_mark_published_on_uses_given_connection(self):
        """mark_published_on must execute on the provided connection."""
        from uuid import uuid4

        conn = AsyncMock()
        event_id = uuid4()

        await OutboxRepository.mark_published_on(conn, event_id=event_id)

        conn.execute.assert_awaited_once()
        call_args = conn.execute.call_args[0]
        assert event_id in call_args

    @pytest.mark.asyncio
    async def test_mark_failed_on_truncates_error(self):
        """Error messages should be truncated to 1024 chars."""
        from uuid import uuid4

        conn = AsyncMock()
        long_error = "x" * 2000

        await OutboxRepository.mark_failed_on(
            conn, event_id=uuid4(), error=long_error
        )

        call_args = conn.execute.call_args[0]
        error_arg = call_args[2]
        assert len(error_arg) <= 1024

    @pytest.mark.asyncio
    async def test_mark_failed_on_handles_none_error(self):
        """None error should become empty string."""
        from uuid import uuid4

        conn = AsyncMock()
        await OutboxRepository.mark_failed_on(
            conn, event_id=uuid4(), error=None
        )

        call_args = conn.execute.call_args[0]
        error_arg = call_args[2]
        assert error_arg == ""

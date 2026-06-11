# app/repositories/outbox_repo.py

import json
from contextlib import asynccontextmanager
from typing import AsyncGenerator, List, Tuple
from uuid import UUID

from orchestration.repositories.base_repo import BaseRepository


class OutboxRepository(BaseRepository):
    """
    Transactional Outbox Repository

    Guarantees:
    - Events are written in the SAME transaction as business data
    - Events are published exactly-once (best-effort)
    - Safe for crashes, restarts, retries
    """

    def __init__(self, db):
        self.db = db

    # -------------------------------------------------
    # WRITE (used inside business transactions)
    # -------------------------------------------------
    async def enqueue(
        self,
        *,
        aggregate_type: str,
        aggregate_id: UUID,
        event_type: str,
        payload: dict,
        tx=None,
    ) -> None:
        """
        Insert an event into the outbox.

        MUST be called inside an existing DB transaction.
        If tx is provided, uses that connection; otherwise uses the pool.
        """
        conn = tx or self.db
        await conn.execute(
            """
            INSERT INTO outbox_events (
                aggregate_type,
                aggregate_id,
                event_type,
                payload,
                status,
                created_at
            )
            VALUES ($1, $2, $3, $4::jsonb, 'PENDING', now())
            """,
            aggregate_type,
            aggregate_id,
            event_type,
            json.dumps(payload),
        )

    # -------------------------------------------------
    # READ + PROCESS (used by outbox worker)
    # -------------------------------------------------
    @asynccontextmanager
    async def fetch_and_lock(
        self,
        *,
        limit: int = 100,
    ) -> AsyncGenerator[Tuple[List[dict], "object"], None]:
        """
        Fetch pending events inside a held transaction.

        Yields (events, conn) — the transaction stays open so that
        FOR UPDATE SKIP LOCKED locks are held until the caller finishes
        marking events as published/failed and the context manager exits.

        Usage::

            async with repo.fetch_and_lock(limit=50) as (events, conn):
                for event in events:
                    await publish(event)
                    await repo.mark_published_on(conn, event_id=event["id"])
            # transaction commits here, locks released
        """
        async with self.db.acquire() as conn:
            async with conn.transaction():
                rows = await conn.fetch(
                    """
                    SELECT id,
                           aggregate_type,
                           aggregate_id,
                           event_type,
                           payload
                    FROM outbox_events
                    WHERE status = 'PENDING'
                    ORDER BY created_at ASC
                    LIMIT $1
                    FOR UPDATE SKIP LOCKED
                    """,
                    limit,
                )
                yield [dict(r) for r in rows], conn

    async def fetch_pending(
        self,
        *,
        limit: int = 100,
    ) -> List[dict]:
        """
        Fetch pending events (legacy convenience method).

        WARNING: locks are released when this returns. Prefer
        fetch_and_lock() for the publish loop.
        """
        async with self.db.acquire() as conn:
            async with conn.transaction():
                rows = await conn.fetch(
                    """
                    SELECT id,
                           aggregate_type,
                           aggregate_id,
                           event_type,
                           payload
                    FROM outbox_events
                    WHERE status = 'PENDING'
                    ORDER BY created_at ASC
                    LIMIT $1
                    FOR UPDATE SKIP LOCKED
                    """,
                    limit,
                )
        return [dict(r) for r in rows]

    # -------------------------------------------------
    # MARK helpers (connection-bound for use inside fetch_and_lock)
    # -------------------------------------------------
    @staticmethod
    async def mark_published_on(conn, *, event_id: UUID) -> None:
        """Mark event as published using the given connection (same txn)."""
        await conn.execute(
            """
            UPDATE outbox_events
            SET status = 'PUBLISHED',
                published_at = now()
            WHERE id = $1
            """,
            event_id,
        )

    @staticmethod
    async def mark_failed_on(conn, *, event_id: UUID, error: str) -> None:
        """Mark event as failed using the given connection (same txn)."""
        error_str = str(error)[:1024] if error else ""
        await conn.execute(
            """
            UPDATE outbox_events
            SET status = 'FAILED',
                error = $2,
                updated_at = now()
            WHERE id = $1
            """,
            event_id,
            error_str,
        )

    # -------------------------------------------------
    # MARK PUBLISHED (pool-level, legacy)
    # -------------------------------------------------
    async def mark_published(
        self,
        *,
        event_id: UUID,
    ) -> None:
        await self.db.execute(
            """
            UPDATE outbox_events
            SET status = 'PUBLISHED',
                published_at = now()
            WHERE id = $1
            """,
            event_id,
        )

    # -------------------------------------------------
    # MARK FAILED (retryable)
    # -------------------------------------------------
    async def mark_failed(
        self,
        *,
        event_id: UUID,
        error: str,
    ) -> None:
        error_str = str(error)[:1024] if error else ""
        await self.db.execute(
            """
            UPDATE outbox_events
            SET status = 'FAILED',
                error = $2,
                updated_at = now()
            WHERE id = $1
            """,
            event_id,
            error_str,
        )

    # -------------------------------------------------
    # HARD FAIL (dead-letter)
    # -------------------------------------------------
    async def mark_dead(
        self,
        *,
        event_id: UUID,
        error: str,
    ) -> None:
        error_str = str(error)[:1024] if error else ""
        await self.db.execute(
            """
            UPDATE outbox_events
            SET status = 'DEAD',
                error = $2,
                updated_at = now()
            WHERE id = $1
            """,
            event_id,
            error_str,
        )

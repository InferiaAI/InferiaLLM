# app/repositories/outbox_repo.py

import json
from typing import List
from uuid import UUID
# from datetime import datetime
from inferia.services.orchestration.repositories.base_repo import BaseRepository


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
        tx=None
    ) -> None:
        """
        Insert an event into the outbox.

        MUST be called inside an existing DB transaction.
        """

        await self.db.execute(
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
    # READ (used by outbox worker)
    # -------------------------------------------------
    async def fetch_pending(
        self,
        *,
        limit: int = 100,
    ) -> List[dict]:
        """
        Fetch pending events for publishing.

        Uses FOR UPDATE SKIP LOCKED to allow parallel workers.
        """

        rows = await self.db.fetch(
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
    # MARK PUBLISHED
    # -------------------------------------------------
    async def mark_published(
        self,
        *,
        event_id: UUID,
    ) -> None:
        """
        Mark event as successfully published.
        """

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
        """
        Mark event as failed but retryable.
        """

        await self.db.execute(
            """
            UPDATE outbox_events
            SET status = 'FAILED',
                error = $2,
                updated_at = now()
            WHERE id = $1
            """,
            event_id,
            error[:1024],
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
        """
        Mark event as dead (manual intervention required).
        """

        await self.db.execute(
            """
            UPDATE outbox_events
            SET status = 'DEAD',
                error = $2,
                updated_at = now()
            WHERE id = $1
            """,
            event_id,
            error[:1024],
        )

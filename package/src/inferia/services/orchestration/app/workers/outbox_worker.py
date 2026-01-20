# app/workers/outbox_worker.py
import asyncio
import json
import logging

logger = logging.getLogger("outbox-worker")


class OutboxWorker:
    def __init__(self, db_pool, event_bus, interval_seconds=1):
        self.db = db_pool
        self.bus = event_bus
        self.interval = interval_seconds

    async def run(self):
        logger.info("Outbox worker started")
        while True:
            try:
                await self._publish_once()
            except Exception:
                logger.exception("Outbox publish failed")
            await asyncio.sleep(self.interval)

    async def _publish_once(self):
        async with self.db.acquire() as conn:
            from repositories.outbox_repo import OutboxRepository
            repo = OutboxRepository(conn)
            
            # The worker previously looked for 'topic', but schema and repo say 'event_type'.
            # Based on schema.sql: event_type text NOT NULL.
            # We must adapt to event_type.
            
            events = await repo.fetch_pending(limit=100)

            for event in events:
                # The payload in repo result is already a dict (jsonb),
                # but original code did json.loads(row["payload"]).
                # OutboxRepository fetch_pending returns [dict(r) for r in rows].
                # asyncpg returns jsonb as string? No, typically behaves based on codec.
                # If jsonb, likely already decoded or string. 
                # Repo implementation: `json.dumps(payload)` on insert.
                # When fetching, asyncpg usually returns str for jsonb unless configured. 
                # But let's check repo fetch_pending logic:
                # `return [dict(r) for r in rows]`
                # Let's assume standard behavior. If payload is dict, good. 
                # If string, we might need load. 
                # However, OutboxPublisher (above) uses event["payload"] directly.
                # We will assume consistency with Publisher change.
                
                payload = event["payload"]
                if isinstance(payload, str):
                   payload = json.loads(payload)
                   
                await self.bus.publish(
                    event["event_type"],
                    payload,
                )

                await repo.mark_published(event_id=event["id"])

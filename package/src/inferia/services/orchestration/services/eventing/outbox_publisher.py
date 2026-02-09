import asyncio
import logging

logger = logging.getLogger(__name__)


from inferia.services.orchestration.repositories.outbox_repo import OutboxRepository

async def outbox_publisher_loop(db, event_bus, *, batch_size=50, interval=0.5):
    """
    Reliable outbox publisher.
    Must run as a background task in the orchestration service.
    """
    repo = OutboxRepository(db)
    logger.info("Outbox publisher started")

    while True:
        try:
            # explicit transaction handling is done inside fetch_pending via DB pool if needed,
            # but here we pass the pool directly. OutboxRepository usually takes a pool or connection.
            # Checking base_repo.py, it takes 'db'.
            # fetch_pending uses self.db.fetch, so passing pool is fine.
            
            # Additional transaction is needed for atomic mark_published if we were doing complex things,
            # but fetch_pending uses FOR UPDATE SKIP LOCKED, so we should commit promptly.
            # However, the repo methods are designed to be called; fetch_pending returns dicts.
            
            # We need a strategy: fetch pending, publish each, mark as published.
            # Ideally we mark published AFTER successful publish.
            
            async with db.acquire() as conn:
                # We use a repo instance bound to the connection for this batch
                # wait, OutboxRepository takes 'db' in init. 
                # If we pass pool, it uses pool. If we pass conn, it uses conn.
                repo_conn = OutboxRepository(conn)
                
                # Fetch pending (locks rows)
                events = await repo_conn.fetch_pending(limit=batch_size)
                
                for event in events:
                    try:
                        await event_bus.publish(
                            event["event_type"],
                            event["payload"],
                        )
                        await repo_conn.mark_published(event_id=event["id"])
                    except Exception as e:
                        logger.error(f"Failed to publish event {event['id']}: {e}")
                        # Optionally mark failed or retry later. 
                        # For now, we just log and it will be picked up again if we didn't update status,
                        # OR we should mark it failed/retryable if we want to avoid head-of-line blocking.
                        # The original code didn't handle failure updates, just caught exception loop-wide.
                        # We will skip marking published so it retries, but we should be careful about poison pills.
                        pass

        except Exception as e:
            # Never crash the process
            logger.exception("Outbox publisher failure: %s", e)

        await asyncio.sleep(interval)

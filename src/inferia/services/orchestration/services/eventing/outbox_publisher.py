import asyncio
import logging

logger = logging.getLogger(__name__)


from inferia.services.orchestration.repositories.outbox_repo import OutboxRepository

async def outbox_publisher_loop(db, event_bus, *, batch_size=50, interval=0.5):
    """
    Reliable outbox publisher.
    Must run as a background task in the orchestration service.

    Uses fetch_and_lock so that FOR UPDATE SKIP LOCKED row locks are
    held for the entire publish cycle, preventing duplicate delivery.
    """
    repo = OutboxRepository(db)
    logger.info("Outbox publisher started")

    while True:
        try:
            async with repo.fetch_and_lock(limit=batch_size) as (events, conn):
                for event in events:
                    try:
                        await event_bus.publish(
                            event["event_type"],
                            event["payload"],
                        )
                        await OutboxRepository.mark_published_on(
                            conn, event_id=event["id"]
                        )
                    except Exception as e:
                        logger.error("Failed to publish event %s: %s", event["id"], e)
                        await OutboxRepository.mark_failed_on(
                            conn, event_id=event["id"], error=str(e)
                        )

        except Exception as e:
            logger.exception("Outbox publisher failure: %s", e)

        await asyncio.sleep(interval)

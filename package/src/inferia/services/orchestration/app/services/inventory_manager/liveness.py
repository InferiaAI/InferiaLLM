import asyncio
import logging
from datetime import datetime, timezone

logger = logging.getLogger("inventory-liveness")

async def liveness_loop(inventory_repo, *, interval_seconds: int = 10):
    """
    Periodically transitions nodes from PROVISIONING -> READY
    once they are assumed booted.

    This simulates (or later integrates with) real health checks.
    """
    while True:
        try:
            updated = await inventory_repo.mark_ready_after_boot()

            if updated:
                logger.info(
                    "Inventory liveness: %d nodes marked READY",
                    updated,
                )

        except Exception as exc:
            logger.exception(
                "Inventory liveness loop failed: %s", exc
            )

        await asyncio.sleep(interval_seconds)

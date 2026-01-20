# app/workers/inventory_liveness_worker.py
import asyncio
import logging
from datetime import timedelta

logger = logging.getLogger("inventory-liveness-worker")


class InventoryLivenessWorker:
    def __init__(self, inventory_repo, interval_seconds=10):
        self.inventory_repo = inventory_repo
        self.interval = interval_seconds

    async def run(self):
        logger.info("Inventory liveness worker started")
        while True:
            try:
                await self.inventory_repo.mark_dead_nodes()
            except Exception:
                logger.exception("Inventory liveness check failed")
            await asyncio.sleep(self.interval)

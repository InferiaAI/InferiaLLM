# app/workers/spot_reclaimer_worker.py
import asyncio
import logging

logger = logging.getLogger("spot-reclaimer-worker")


class SpotReclaimerWorker:
    def __init__(self, spot_reclaimer, interval_seconds=300):
        self.reclaimer = spot_reclaimer
        self.interval = interval_seconds

    async def run(self):
        logger.info("Spot reclaimer worker started")
        while True:
            try:
                await self.reclaimer.reclaim()
            except Exception:
                logger.exception("Spot reclaim failed")
            await asyncio.sleep(self.interval)

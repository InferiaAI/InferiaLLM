# app/workers/autoscaler_worker.py
import asyncio
import logging

logger = logging.getLogger("autoscaler-worker")


class AutoscalerWorker:
    def __init__(self, autoscaler_engine, interval_seconds=10):
        self.autoscaler = autoscaler_engine
        self.interval = interval_seconds

    async def run(self):
        logger.info("Autoscaler worker started")
        while True:
            try:
                await self.autoscaler.tick()
            except Exception:
                logger.exception("Autoscaler tick failed")
            await asyncio.sleep(self.interval)

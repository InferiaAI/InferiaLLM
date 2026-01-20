import asyncpg
import logging
from uuid import UUID

log = logging.getLogger(__name__)

class FiltrationSyncRepository:
    """
    Repository to synchronize deployment data with the external Filtration service database.
    """
    def __init__(self, dsn: str):
        self.dsn = dsn
        self._pool = None

    async def _get_pool(self):
        if not self._pool:
            self._pool = await asyncpg.create_pool(
                dsn=self.dsn,
                min_size=1,
                max_size=5,
                ssl=False
            )
        return self._pool

    async def update_deployment_endpoint(self, deployment_id: UUID, endpoint_url: str):
        """Update the endpoint_url in the filtration deployments table."""
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            # Sync to model_deployments (shared DB)
            status = await conn.execute(
                "UPDATE model_deployments SET endpoint = $2, updated_at = now() WHERE deployment_id = $1",
                str(deployment_id),
                endpoint_url
            )
            if status == "UPDATE 1":
                log.info(f"Successfully synced endpoint_url for deployment {deployment_id} to {endpoint_url}")
            else:
                log.warning(f"Could not find deployment {deployment_id} in filtration database to sync endpoint_url")
            return status == "UPDATE 1"

    async def close(self):
        if self._pool:
            await self._pool.close()
            self._pool = None

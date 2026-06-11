import os
import asyncpg
import logging
from uuid import UUID
from typing import Optional

log = logging.getLogger(__name__)


class ApiGatewaySyncRepository:
    """
    Repository to synchronize deployment data with the API Gateway service database.
    """

    def __init__(self, dsn: str, ssl: Optional[bool] = None):
        self.dsn = dsn
        self._pool = None
        # SSL is enabled by default for production security
        # Can be disabled via DATABASE_SSL=false environment variable for local development
        if ssl is None:
            self.ssl = os.getenv("DATABASE_SSL", "true").lower() != "false"
        else:
            self.ssl = ssl

    async def _get_pool(self):
        if not self._pool:
            log.info(
                f"Creating database connection pool (SSL={'enabled' if self.ssl else 'disabled'})"
            )
            self._pool = await asyncpg.create_pool(
                dsn=self.dsn, min_size=1, max_size=5, ssl=self.ssl
            )
        return self._pool

    async def update_deployment_endpoint(self, deployment_id: UUID, endpoint_url: str):
        """Update the endpoint_url in the API Gateway deployments table."""
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            # Sync to model_deployments (shared DB)
            status = await conn.execute(
                "UPDATE model_deployments SET endpoint = $2, updated_at = now() WHERE deployment_id = $1",
                str(deployment_id),
                endpoint_url,
            )
            if status == "UPDATE 1":
                log.info(
                    f"Successfully synced endpoint_url for deployment {deployment_id} to {endpoint_url}"
                )
            else:
                log.warning(
                    f"Could not find deployment {deployment_id} in API Gateway database to sync endpoint_url"
                )
            return status == "UPDATE 1"

    async def close(self):
        if self._pool:
            await self._pool.close()
            self._pool = None

import json
import logging
from uuid import UUID

logger = logging.getLogger(__name__)


class ComputePoolRepository:
    def __init__(self, db):
        self.db = db

    async def get_provider_config(self, provider: str) -> dict:
        """
        Read provider config from system_settings (shared DB with API Gateway).
        Returns the config dict for the given provider, or empty dict.
        """
        query = """
        SELECT value FROM system_settings WHERE key = 'providers_config' LIMIT 1
        """
        try:
            async with self.db.acquire() as conn:
                raw = await conn.fetchval(query)
                if not raw:
                    return {}
                data = json.loads(raw) if isinstance(raw, str) else raw
                providers = data.get("providers", data)

                # Map provider to config path
                config_paths = {
                    "gcp": ("cloud", "gcp"),
                    "aws": ("cloud", "aws"),
                    "nosana": ("depin", "nosana"),
                    "akash": ("depin", "akash"),
                }
                path = config_paths.get(provider)
                if not path:
                    return {}
                section = providers
                for key in path:
                    section = section.get(key, {})
                return section
        except Exception as e:
            logger.warning(f"Could not read provider config from DB: {e}")
            return {}

    async def credential_exists(self, provider: str, credential_name: str) -> bool:
        """
        Check if a credential exists for the given provider.
        This validates against the provider_credentials table.
        """
        query = """
        SELECT EXISTS(
            SELECT 1 FROM provider_credentials 
            WHERE provider = $1 AND name = $2 AND is_active = TRUE
        )
        """
        async with self.db.acquire() as conn:
            return await conn.fetchval(query, provider, credential_name)

    async def create_pool(self, data: dict):
        query = """
        INSERT INTO compute_pools (
            pool_name,
            owner_type,
            owner_id,
            provider,
            pool_type,
            allowed_gpu_types,
            max_cost_per_hour,
            is_dedicated,
            scheduling_policy,
            provider_pool_id,
            provider_credential_name,
            cluster_id,
            region_constraint,
            is_active,
            lifecycle_state,
            gpu_count
        )
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16)
        RETURNING id
        """
        async with self.db.acquire() as conn:
            return await conn.fetchval(
                query,
                data["pool_name"],
                data["owner_type"],
                data["owner_id"],
                data["provider"],
                data.get("pool_type", "job"),
                data["allowed_gpu_types"],
                data["max_cost_per_hour"],
                data["is_dedicated"],
                data["scheduling_policy"],
                data["provider_pool_id"],
                data.get("provider_credential_name"),
                data.get("cluster_id"),
                data.get("region_constraint"),
                data.get("is_active", True),
                data.get("lifecycle_state", "running"),
                data.get("gpu_count", 1),
            )

    async def update_pool(self, pool_id: UUID, data: dict):
        query = """
        UPDATE compute_pools
        SET allowed_gpu_types = $2,
            max_cost_per_hour = $3,
            is_dedicated = $4,
            updated_at = now()
        WHERE id = $1 AND is_active = TRUE
        """
        async with self.db.acquire() as conn:
            await conn.execute(
                query,
                pool_id,
                data["allowed_gpu_types"],
                data["max_cost_per_hour"],
                data["is_dedicated"],
            )

    async def soft_delete_pool(self, pool_id: UUID):
        query = """
        UPDATE compute_pools
        SET is_active = FALSE,
            updated_at = now()
        WHERE id = $1
        """
        async with self.db.acquire() as conn:
            await conn.execute(query, pool_id)

    async def bind_provider_resource(
        self, pool_id: UUID, provider_resource_id: UUID, priority: int
    ):
        query = """
        INSERT INTO compute_pool_provider_resources
            (pool_id, provider_resource_id, priority)
        VALUES ($1, $2, $3)
        ON CONFLICT (pool_id, provider_resource_id)
        DO UPDATE SET priority = EXCLUDED.priority,
                      is_enabled = TRUE
        """
        async with self.db.acquire() as conn:
            await conn.execute(query, pool_id, provider_resource_id, priority)

    async def unbind_provider_resource(self, pool_id: UUID, provider_resource_id: UUID):
        query = """
        UPDATE compute_pool_provider_resources
        SET is_enabled = FALSE
        WHERE pool_id = $1 AND provider_resource_id = $2
        """
        async with self.db.acquire() as conn:
            await conn.execute(query, pool_id, provider_resource_id)

    async def list_pool_inventory(self, pool_id: UUID):
        query = """
        SELECT
            ci.id AS node_id,
            ci.provider,
            ci.state,
            ci.gpu_total,
            ci.gpu_allocated,
            ci.vcpu_total,
            ci.vcpu_allocated,
            ci.last_heartbeat,
            ci.created_at,
            ci.expose_url
        FROM compute_inventory ci
        WHERE ci.pool_id = $1
        """
        async with self.db.acquire() as conn:
            return await conn.fetch(query, pool_id)

    async def get(self, pool_id: UUID):
        query = """
        SELECT *
        FROM compute_pools
        WHERE id = $1 AND is_active = TRUE
        """
        async with self.db.acquire() as conn:
            return await conn.fetchrow(query, pool_id)

    async def list_pools(self, owner_id: str | None = None):
        query = """
        SELECT
            id,
            pool_name,
            provider,
            pool_type,
            is_active,
            owner_type,
            owner_id,
            max_cost_per_hour,
            allowed_gpu_types,
            is_dedicated,
            scheduling_policy,
            provider_pool_id,
            provider_credential_name,
            cluster_id,
            region_constraint,
            lifecycle_state,
            gpu_count,
            updated_at,
            created_at
        FROM compute_pools
        WHERE is_active = TRUE
        """

        params = []

        if owner_id:
            query += " AND owner_id = $1"
            params.append(owner_id)

        async with self.db.acquire() as conn:
            return await conn.fetch(query, *params)

    async def update_pool_cluster_id(self, pool_id: UUID, cluster_id: str):
        """Update the cluster_id for a cluster-based pool."""
        query = """
        UPDATE compute_pools
        SET cluster_id = $2,
            updated_at = now()
        WHERE id = $1
        """
        async with self.db.acquire() as conn:
            await conn.execute(query, pool_id, cluster_id)

    async def get_cluster_id(self, pool_id: UUID) -> str | None:
        """Get the cluster_id for a pool."""
        query = """
        SELECT cluster_id
        FROM compute_pools
        WHERE id = $1
        """
        async with self.db.acquire() as conn:
            return await conn.fetchval(query, pool_id)
    async def set_pool_active(self, pool_id: UUID, is_active: bool):
        """Set the active status of a pool."""
        query = """
        UPDATE compute_pools
        SET is_active = $2,
            updated_at = now()
        WHERE id = $1
        """
        async with self.db.acquire() as conn:
            await conn.execute(query, pool_id, is_active)

    async def set_pool_lifecycle_state(self, pool_id: UUID, lifecycle_state: str):
        query = """
        UPDATE compute_pools
        SET lifecycle_state = $2,
            updated_at = now()
        WHERE id = $1 AND is_active = TRUE
        """
        async with self.db.acquire() as conn:
            await conn.execute(query, pool_id, lifecycle_state)

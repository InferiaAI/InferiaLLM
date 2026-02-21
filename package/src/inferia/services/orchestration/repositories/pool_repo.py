from uuid import UUID


class ComputePoolRepository:
    def __init__(self, db):
        self.db = db

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
            allowed_gpu_types,
            max_cost_per_hour,
            is_dedicated,
            scheduling_policy,
            provider_pool_id,
            provider_credential_name
        )
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
        RETURNING id
        """
        async with self.db.acquire() as conn:
            return await conn.fetchval(
                query,
                data["pool_name"],
                data["owner_type"],
                data["owner_id"],
                data["provider"],
                data["allowed_gpu_types"],
                data["max_cost_per_hour"],
                data["is_dedicated"],
                data["scheduling_policy"],
                data["provider_pool_id"],
                data.get("provider_credential_name"),
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
            is_active,
            owner_type,
            owner_id,
            max_cost_per_hour,
            allowed_gpu_types,
            is_dedicated,
            scheduling_policy,
            provider_pool_id,
            provider_credential_name,
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

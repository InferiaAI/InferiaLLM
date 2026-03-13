from uuid import UUID

EPHEMERAL_PROVIDERS = {"nosana", "akash"}


class PlacementRepository:
    def __init__(self, db):
        self.db = db

    async def fetch_candidate_nodes(
        self,
        pool_id: UUID,
        gpu_req: int,
        vcpu_req: int = 0,
        ram_req: int = 0,
    ):
        async with self.db.acquire() as conn:
            pool_row = await conn.fetchrow(
                "SELECT provider FROM compute_pools WHERE id = $1",
                pool_id,
            )
            if not pool_row:
                return []
            provider = pool_row["provider"]
            is_ephemeral = provider.lower() in EPHEMERAL_PROVIDERS

        base_query = """
        SELECT
            ci.id AS node_id,
            ci.provider,
            ci.state,
            (ci.gpu_total - ci.gpu_allocated) AS gpu_free,
            (ci.vcpu_total - ci.vcpu_allocated) AS vcpu_free,
            (ci.ram_gb_total - ci.ram_gb_allocated) AS ram_free,
            ci.health_score
        FROM compute_inventory ci
        JOIN compute_pools cp ON cp.id = ci.pool_id
        WHERE
            ci.pool_id = $1
            AND cp.is_active = TRUE
            AND ci.state = 'ready'
            AND (ci.gpu_total - ci.gpu_allocated) >= $2
            AND (ci.vcpu_total - ci.vcpu_allocated) >= $3
            AND (ci.ram_gb_total - ci.ram_gb_allocated) >= $4
            AND (
                COALESCE(ci.node_class, '') = 'cluster'
                OR (ci.last_heartbeat IS NOT NULL AND ci.last_heartbeat > now() - INTERVAL '2 minutes')
                OR (ci.last_heartbeat IS NULL AND ci.created_at > now() - INTERVAL '2 minutes')
            )
        """

        if is_ephemeral:
            ephemeral_filter = """
            AND NOT EXISTS (
                SELECT 1 FROM model_deployments md
                WHERE md.node_ids @> ARRAY[ci.id]::uuid[]
                AND md.state IN ('RUNNING', 'READY', 'PROVISIONING')
            )
            """
            query = base_query + ephemeral_filter
        else:
            query = base_query

        async with self.db.acquire() as conn:
            return await conn.fetch(
                query,
                pool_id,
                gpu_req,
                vcpu_req,
                ram_req,
            )

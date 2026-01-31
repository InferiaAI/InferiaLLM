import json

class ComputeNodeRepository:

    def __init__(self, db):
        self.db = db

    async def register_node(
        self,
        pool_name: str,
        owner_type: str,
        owner_id: str,
        provider: str,
        allowed_gpu_types: list,
        max_cost_per_hour: float,
        is_dedicated: bool,
        scheduling_policy_json: dict,        
    ):
        query = """
        INSERT INTO compute_pools (
            pool_name, owner_type, owner_id,
            provider, allowed_gpu_types,
            max_cost_per_hour, is_dedicated,
            scheduling_policy
        )
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
        RETURNING id
        """
        async with self.db.acquire() as conn:
            return await conn.fetchval(
                query,
                pool_name,
                owner_type,
                owner_id,
                provider,
                allowed_gpu_types,
                max_cost_per_hour,
                is_dedicated,
                json.dumps(scheduling_policy_json)
            )

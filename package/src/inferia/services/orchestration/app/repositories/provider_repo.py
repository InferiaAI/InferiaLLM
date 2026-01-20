class ProviderResourceRepository:

    def __init__(self, db):
        self.db = db

    async def upsert_provider_resource(self, data: dict):
        query = """
        INSERT INTO provider_resources (
            provider, provider_resource_id,
            gpu_type, gpu_count, gpu_memory_gb,
            vcpu, ram_gb, region,
            pricing_model, price_per_hour
        )
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
        ON CONFLICT (provider, provider_resource_id, region)
        DO UPDATE SET
            price_per_hour = EXCLUDED.price_per_hour,
            updated_at = now()
        """
        async with self.db.acquire() as conn:
            await conn.execute(
                query,
                data["provider"],
                data["provider_resource_id"],
                data["gpu_type"],
                data["gpu_count"],
                data["gpu_memory_gb"],
                data["vcpu"],
                data["ram_gb"],
                data["region"],
                data["price_per_hour"],
            )

    async def list_provider_resources(self, provider: str = None) -> list[dict]:
        query = """
        SELECT
            provider, provider_resource_id,
            gpu_type, gpu_count, gpu_memory_gb,
            vcpu, ram_gb, region,
            pricing_model, price_per_hour
        FROM provider_resources
        """
        args = []
        if provider:
            query += " WHERE provider = $1"
            args.append(provider)

        async with self.db.acquire() as conn:
            rows = await conn.fetch(query, *args)
            return [dict(r) for r in rows]

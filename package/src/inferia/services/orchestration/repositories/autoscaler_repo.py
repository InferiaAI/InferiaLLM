class AutoscalerRepository:
    def __init__(self, db, event_bus):
        self.db = db
        self.event_bus = event_bus

    async def get_pools(self):
        q = """
        SELECT id, provider, autoscaling_policy
        FROM compute_pools
        WHERE is_active=true
          AND autoscaling_policy->>'enabled'='true'
        """
        async with self.db.acquire() as c:
            return await c.fetch(q)

    async def pool_stats(self, pool_id):
        q = """
        SELECT
          COUNT(*) FILTER (WHERE state='ready') AS ready_nodes,
          COALESCE(AVG(vcpu_allocated::float / NULLIF(vcpu_total,0)),0) AS avg_cpu_util,
          COUNT(*) FILTER (WHERE state='ready' AND vcpu_allocated=0) AS idle_nodes
        FROM compute_inventory
        WHERE pool_id=$1
        """
        async with self.db.acquire() as c:
            return await c.fetchrow(q, pool_id)

    async def state(self, pool_id):
        async with self.db.acquire() as c:
            await c.execute(
                """
                INSERT INTO autoscaler_state (pool_id)
                VALUES ($1)
                ON CONFLICT (pool_id) DO NOTHING
                """,
                pool_id,
            )
            return await c.fetchrow(
                "SELECT * FROM autoscaler_state WHERE pool_id=$1",
                pool_id,
            )

    async def record_scale(self, pool_id):
        async with self.db.acquire() as c:
            await c.execute(
                "UPDATE autoscaler_state SET last_scale_at=now() WHERE pool_id=$1",
                pool_id,
            )

        await self.event_bus.publish(
            "autoscaler.scale_recorded",
            {"pool_id": str(pool_id)},
        )

    async def incr_failures(self, pool_id):
        async with self.db.acquire() as c:
            await c.execute(
                """
                UPDATE autoscaler_state
                SET consecutive_failures=consecutive_failures+1
                WHERE pool_id=$1
                """,
                pool_id,
            )

        await self.event_bus.publish(
            "autoscaler.failure_incremented",
            {"pool_id": str(pool_id)},
        )

    async def reset_failures(self, pool_id):
        async with self.db.acquire() as c:
            await c.execute(
                """
                UPDATE autoscaler_state
                SET consecutive_failures=0
                WHERE pool_id=$1
                """,
                pool_id,
            )

        await self.event_bus.publish(
            "autoscaler.failure_reset",
            {"pool_id": str(pool_id)},
        )

    async def mark_draining(self, node_id):
        async with self.db.acquire() as c:
            await c.execute(
                "UPDATE compute_inventory SET state='draining' WHERE id=$1",
                node_id,
            )

        await self.event_bus.publish(
            "node.marked_draining",
            {"node_id": str(node_id)},
        )

import logging

logger = logging.getLogger(__name__)

BATCH_SIZE = 100


class SpotReclaimer:
    def __init__(self, db):
        self.db = db

    async def reclaim(self):
        """
        Reclaim resources from spot nodes that have been terminated.
        Processes in batches with SKIP LOCKED to avoid global lock contention.
        """
        total_reclaimed = 0

        while True:
            reclaimed = await self._reclaim_batch()
            total_reclaimed += reclaimed
            if reclaimed < BATCH_SIZE:
                break

        if total_reclaimed:
            logger.info("Reclaimed %d spot allocations", total_reclaimed)

        return total_reclaimed

    async def _reclaim_batch(self) -> int:
        async with self.db.acquire() as conn:
            async with conn.transaction():
                victims = await conn.fetch(
                    """
                    SELECT a.allocation_id,
                           a.node_id,
                           a.gpu,
                           a.vcpu,
                           a.ram_gb,
                           a.owner_type,
                           a.owner_id
                    FROM allocations a
                    JOIN compute_inventory n ON a.node_id = n.id
                    WHERE n.node_class = 'spot'
                      AND n.state = 'terminated'
                    LIMIT $1
                    FOR UPDATE SKIP LOCKED
                    """,
                    BATCH_SIZE,
                )

                for v in victims:
                    await conn.execute(
                        """
                        UPDATE compute_inventory
                        SET
                          gpu_allocated = gpu_allocated - $2,
                          vcpu_allocated = vcpu_allocated - $3,
                          ram_gb_allocated = ram_gb_allocated - $4
                        WHERE id=$1
                        """,
                        v["node_id"],
                        v["gpu"],
                        v["vcpu"],
                        v["ram_gb"],
                    )

                    await conn.execute(
                        """
                        INSERT INTO billing_events (
                            owner_type,
                            owner_id,
                            allocation_id,
                            node_id,
                            event_type,
                            gpu,
                            vcpu,
                            ram_gb,
                            cost
                        )
                        VALUES ($1,$2,$3,$4,'SPOT_RECLAIM',$5,$6,$7,0)
                        """,
                        v["owner_type"],
                        v["owner_id"],
                        v["allocation_id"],
                        v["node_id"],
                        v["gpu"],
                        v["vcpu"],
                        v["ram_gb"],
                    )

                    await conn.execute(
                        "DELETE FROM allocations WHERE allocation_id=$1",
                        v["allocation_id"],
                    )

                return len(victims)

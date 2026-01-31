from uuid import UUID


class JobRepository:
    def __init__(self, db):
        self.db = db

    async def get_job(self, job_id: UUID):
        async with self.db.acquire() as conn:
            return await conn.fetchrow(
                """
                SELECT job_id, owner_type, owner_id, gang_size, state, created_at
                FROM gang_jobs
                WHERE job_id=$1
                """,
                job_id,
            )

    async def list_jobs(self, owner_type: str, owner_id: str):
        async with self.db.acquire() as conn:
            return await conn.fetch(
                """
                SELECT job_id, owner_type, owner_id, gang_size, state, created_at
                FROM gang_jobs
                WHERE owner_type=$1 AND owner_id=$2
                ORDER BY created_at DESC
                """,
                owner_type,
                owner_id,
            )

    async def list_allocations(self, job_id: UUID):
        async with self.db.acquire() as conn:
            return await conn.fetch(
                """
                SELECT allocation_id, node_id, gpu, vcpu, ram_gb, priority
                FROM allocations
                WHERE job_id=$1
                """,
                job_id,
            )

    async def cancel_job(self, job_id: UUID):
        async with self.db.acquire() as conn:
            async with conn.transaction():

                job = await conn.fetchrow(
                    "SELECT state FROM gang_jobs WHERE job_id=$1 FOR UPDATE",
                    job_id,
                )

                if not job or job["state"] not in ("pending", "running"):
                    return False

                # Release allocations (reuse your scheduler logic semantics)
                allocs = await conn.fetch(
                    """
                    SELECT allocation_id, node_id, gpu, vcpu, ram_gb
                    FROM allocations
                    WHERE job_id=$1
                    """,
                    job_id,
                )

                for a in allocs:
                    await conn.execute(
                        """
                        UPDATE compute_inventory
                        SET
                          gpu_allocated = gpu_allocated - $2,
                          vcpu_allocated = vcpu_allocated - $3,
                          ram_gb_allocated = ram_gb_allocated - $4
                        WHERE id=$1
                        """,
                        a["node_id"],
                        a["gpu"],
                        a["vcpu"],
                        a["ram_gb"],
                    )

                await conn.execute(
                    "DELETE FROM allocations WHERE job_id=$1",
                    job_id,
                )

                await conn.execute(
                    "UPDATE gang_jobs SET state='cancelled' WHERE job_id=$1",
                    job_id,
                )

                return True

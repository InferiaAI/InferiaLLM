import asyncio
from uuid import UUID
import uuid
from infra.db import create_test_db_pool
from infra.test_setup import (
    scheduler_stub,
    create_test_pool,
    create_ready_node,
)
from v1 import scheduler_pb2


async def test_gang_concurrency():
    db = await create_test_db_pool()
    stub = await scheduler_stub()

    pool_id = await create_test_pool(db)

    job1_id = uuid.uuid4().hex
    job2_id = uuid.uuid4().hex

    nodes = [
        await create_ready_node(db, pool_id, vcpu=1),
        await create_ready_node(db, pool_id, vcpu=1),
    ]

    async def allocate(job_id):
        return await stub.AllocateGang(
            scheduler_pb2.AllocateGangRequest(
                job_id=job_id,
                node_ids=[str(n) for n in nodes],
                gpu=0,
                vcpu=1,
                ram_gb=1,
                priority=100,
                owner_type="user",
                owner_id="gang-user",
            )
        )

    r1, r2 = await asyncio.gather(
        allocate(job1_id),
        allocate(job2_id),
    )

    successes = sum(1 for r in (r1, r2) if r.success)

    assert successes == 1

    total_allocs = await db.fetchval(
        """
        SELECT COUNT(*) FROM allocations
        WHERE job_id IN ($1, $2)
        """,
        UUID(job1_id),
        UUID(job2_id),
    )
    
    assert total_allocs == 2  # exactly one gang of size 2

    print("✔ Gang concurrency protection verified")


if __name__ == "__main__":
    asyncio.run(test_gang_concurrency())

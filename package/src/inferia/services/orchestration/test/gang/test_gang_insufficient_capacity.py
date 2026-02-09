import asyncio
from uuid import UUID
import uuid
from inferia.services.orchestration.infra.db import create_test_db_pool
from inferia.services.orchestration.infra.test_setup import (
    scheduler_stub,
    create_test_pool,
    create_ready_node,
)
from inferia.services.orchestration.v1 import scheduler_pb2


async def test_gang_insufficient_capacity():
    db = await create_test_db_pool()
    stub = await scheduler_stub()

    pool_id = await create_test_pool(db)

    # One node has insufficient RAM
    n1 = await create_ready_node(db, pool_id, ram_gb=4)
    n2 = await create_ready_node(db, pool_id, ram_gb=4)
    n3 = await create_ready_node(db, pool_id, ram_gb=0)  # insufficient

    job_id = uuid.uuid4().hex

    resp = await stub.AllocateGang(
        scheduler_pb2.AllocateGangRequest(
            job_id=job_id,
            node_ids=[str(n1), str(n2), str(n3)],
            gpu=0,
            vcpu=1,
            ram_gb=1,
            priority=100,
            owner_type="user",
            owner_id="gang-user",
        )
    )

    assert not resp.success
    assert resp.reason == "INSUFFICIENT_CAPACITY"

    allocs = await db.fetchval(
        "SELECT COUNT(*) FROM allocations WHERE job_id=$1",
        UUID(job_id)
    )

    assert allocs == 0

    state = await db.fetchval(
        "SELECT state FROM gang_jobs WHERE job_id=$1",
        UUID(job_id)
    )

    assert state == "failed"

    print("✔ Gang allocation rolled back correctly")


if __name__ == "__main__":
    asyncio.run(test_gang_insufficient_capacity())

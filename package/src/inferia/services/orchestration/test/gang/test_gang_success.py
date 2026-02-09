import asyncio
import uuid
from uuid import UUID
from inferia.services.orchestration.infra.db import create_test_db_pool
from inferia.services.orchestration.infra.test_setup import (
    scheduler_stub,
    create_test_pool,
    create_ready_node,
)
from inferia.services.orchestration.v1 import scheduler_pb2


async def test_gang_success():
    db = await create_test_db_pool()
    stub = await scheduler_stub()

    pool_id = await create_test_pool(db)

    nodes = [
        await create_ready_node(db, pool_id),
        await create_ready_node(db, pool_id),
        await create_ready_node(db, pool_id),
    ]

    job_id = uuid.uuid4().hex

    resp = await stub.AllocateGang(
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

    assert resp.success, resp.reason

    count = await db.fetchval(
        "SELECT COUNT(*) FROM allocations WHERE job_id=$1",
        UUID(job_id)
    )

    assert count == 3

    state = await db.fetchval(
        "SELECT state FROM gang_jobs WHERE job_id=$1",
        UUID(job_id)
    )

    assert state == "running"

    print("✔ Gang allocation succeeded atomically")


if __name__ == "__main__":
    asyncio.run(test_gang_success())

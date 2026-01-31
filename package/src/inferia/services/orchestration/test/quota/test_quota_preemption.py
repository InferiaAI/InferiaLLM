import asyncio
import uuid
from infra.db import create_test_db_pool
from v1 import scheduler_pb2
from infra.test_setup import scheduler_stub


async def test_quota_preemption():
    db = await create_test_db_pool()
    stub = await scheduler_stub()

    owner_type = "org"
    owner_id = f"org-{uuid.uuid4()}"

    # Hard quota = 1 vCPU
    await db.execute(
        """
        INSERT INTO quotas (owner_type, owner_id, max_vcpu)
        VALUES ($1,$2,1)
        """,
        owner_type, owner_id
    )

    # Low priority job
    await stub.Allocate(
        scheduler_pb2.AllocateRequest(
            allocation_id=uuid.uuid4().hex,
            node_id="31bd27f3-7d69-4756-8b21-e54ccb1a0dec",
            gpu=0,
            vcpu=1,
            ram_gb=1,
            priority=10,
            owner_type=owner_type,
            owner_id=owner_id,
        )
    )

    # High priority job → should PREEMPT
    resp = await stub.Allocate(
        scheduler_pb2.AllocateRequest(
            allocation_id=uuid.uuid4().hex,
            node_id="31bd27f3-7d69-4756-8b21-e54ccb1a0dec",
            gpu=0,
            vcpu=1,
            ram_gb=1,
            priority=500,
            owner_type=owner_type,
            owner_id=owner_id,
        )
    )

    assert resp.success
    assert resp.reason == "PREEMPTED_AND_ALLOCATED"

    print("✔ Preemption bypasses quota")


if __name__ == "__main__":
    asyncio.run(test_quota_preemption())

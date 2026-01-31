import asyncio

from infra.db import create_test_db_pool
from v1 import scheduler_pb2
from infra.test_setup import scheduler_stub
import uuid


async def test_hard_quota():
    db = await create_test_db_pool()
    stub = await scheduler_stub()

    owner_type = "user"
    owner_id = f"user-{uuid.uuid4()}"

    await db.execute(
        """
        INSERT INTO quotas (owner_type, owner_id, max_vcpu)
        VALUES ($1,$2,1)
        """,
        owner_type, owner_id
    )

    resp1 = await stub.Allocate(
        scheduler_pb2.AllocateRequest(
            allocation_id=uuid.uuid4().hex,
            node_id="31bd27f3-7d69-4756-8b21-e54ccb1a0dec",
            gpu=0,
            vcpu=1,
            ram_gb=1,
            priority=100,
            owner_type=owner_type,
            owner_id=owner_id,
        )
    )

    assert resp1.success

    resp2 = await stub.Allocate(
        scheduler_pb2.AllocateRequest(
            allocation_id=uuid.uuid4().hex,
            node_id="31bd27f3-7d69-4756-8b21-e54ccb1a0dec",
            gpu=0,
            vcpu=1,
            ram_gb=1,
            priority=100,
            owner_type=owner_type,
            owner_id=owner_id,
        )
    )

    assert not resp2.success
    assert resp2.reason == "VCPU_QUOTA_EXCEEDED"

    print("✔ Hard quota enforcement works")


if __name__ == "__main__":
    asyncio.run(test_hard_quota())

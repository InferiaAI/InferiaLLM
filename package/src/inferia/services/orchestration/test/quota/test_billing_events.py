import asyncio
import uuid
from infra.db import create_test_db_pool
from v1 import scheduler_pb2
from infra.test_setup import scheduler_stub


async def test_billing_events():
    db = await create_test_db_pool()
    stub = await scheduler_stub()

    owner_type = "user"
    owner_id = f"user-{uuid.uuid4()}"
    alloc_id = uuid.uuid4().hex

    await stub.Allocate(
        scheduler_pb2.AllocateRequest(
            allocation_id=alloc_id,
            node_id="31bd27f3-7d69-4756-8b21-e54ccb1a0dec",
            gpu=0,
            vcpu=1,
            ram_gb=1,
            priority=100,
            owner_type=owner_type,
            owner_id=owner_id,
        )
    )

    await stub.Release(
        scheduler_pb2.ReleaseRequest(allocation_id=alloc_id)
    )

    rows = await db.fetch(
        """
        SELECT event_type FROM billing_events
        WHERE allocation_id=$1
        """,
        alloc_id
    )

    events = {r["event_type"] for r in rows}

    assert "ALLOCATE" in events
    assert "RELEASE" in events

    print("✔ Billing events emitted correctly")


if __name__ == "__main__":
    asyncio.run(test_billing_events())

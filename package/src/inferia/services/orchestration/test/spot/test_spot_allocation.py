import asyncio
import uuid
from inferia.services.orchestration.infra.db import create_test_db_pool
from inferia.services.orchestration.infra.test_setup import scheduler_stub, create_test_pool
from inferia.services.orchestration.v1 import scheduler_pb2


async def test_spot_allocation_is_preemptible():
    db = await create_test_db_pool()
    pool_id = await create_test_pool(db, provider="k8s")
    stub = await scheduler_stub()

    # Create SPOT node
    node_id = await db.fetchval(
        """
        INSERT INTO compute_inventory (
            pool_id,
            provider,
            provider_instance_id,
            state,
            node_class,
            price_multiplier,
            gpu_total, gpu_allocated,
            vcpu_total, vcpu_allocated,
            ram_gb_total, ram_gb_allocated
        )
        VALUES (
            $1,
            'k8s',
            $2,
            'ready',
            'spot',
            0.3,
            0,0,
            2,0,
            4,0
        )
        RETURNING id
        """,
        pool_id,
        f"spot-node-{uuid.uuid4()}",
    )

    alloc_id = uuid.uuid4().hex

    resp = await stub.Allocate(
        scheduler_pb2.AllocateRequest(
            allocation_id=str(alloc_id),
            node_id=str(node_id),
            gpu=0,
            vcpu=1,
            ram_gb=1,
            priority=100,
            owner_type="user",
            owner_id="user-spot-test",
        )
    )

    assert resp.success

    row = await db.fetchrow(
        "SELECT preemptible, node_class FROM allocations WHERE allocation_id=$1",
        alloc_id
    )

    assert row["preemptible"] is True
    assert row["node_class"] == "spot"

    print("✔ Spot allocations are always preemptible")


if __name__ == "__main__":
    asyncio.run(test_spot_allocation_is_preemptible())

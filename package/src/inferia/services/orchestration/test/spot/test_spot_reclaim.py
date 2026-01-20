import asyncio
import uuid
from infra.db import create_test_db_pool
from infra.test_setup import create_test_pool
from infra.spot_reclaimer import SpotReclaimer


async def test_spot_reclaimer():
    db = await create_test_db_pool()
    pool_id = await create_test_pool(db, provider="k8s")
    reclaimer = SpotReclaimer(db)

    # Create SPOT node
    node_id = await db.fetchval(
        """
        INSERT INTO compute_inventory (
            pool_id, provider, provider_instance_id,
            state, node_class,
            gpu_total, gpu_allocated,
            vcpu_total, vcpu_allocated,
            ram_gb_total, ram_gb_allocated
        )
        VALUES (
            $1, 'k8s', $2,
            'ready', 'spot',
            0,0,
            2,1,
            4,1
        )
        RETURNING id
        """,
        pool_id,
        f"spot-node-{uuid.uuid4()}",
    )

    alloc_id = uuid.uuid4().hex

    # Insert allocation manually
    await db.execute(
        """
        INSERT INTO allocations (
            allocation_id, node_id,
            gpu, vcpu, ram_gb,
            priority, preemptible,
            node_class,
            owner_type, owner_id
        )
        VALUES ($1,$2,0,1,1,10,TRUE,'spot','user','spot-user')
        """,
        alloc_id, node_id
    )

    # Reclaim
    await reclaimer.reclaim()

    gone = await db.fetchval(
        "SELECT COUNT(*) FROM allocations WHERE allocation_id=$1",
        alloc_id
    )

    assert gone == 0

    print("✔ Spot reclaimer evicts spot allocations")


if __name__ == "__main__":
    asyncio.run(test_spot_reclaimer())

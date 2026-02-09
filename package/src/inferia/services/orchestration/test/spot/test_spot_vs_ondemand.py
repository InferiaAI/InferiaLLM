import asyncio
import uuid
from inferia.services.orchestration.infra.db import create_test_db_pool
from inferia.services.orchestration.infra.test_setup import create_test_pool
from inferia.services.orchestration.infra.spot_reclaimer import SpotReclaimer


async def test_spot_vs_ondemand():
    db = await create_test_db_pool()
    pool_id = await create_test_pool(db, provider="k8s")
    reclaimer = SpotReclaimer(db)

    # SPOT node
    spot_node = await db.fetchval(
        """
        INSERT INTO compute_inventory (
            pool_id, provider, provider_instance_id,
            state, node_class,
            gpu_total, gpu_allocated,
            vcpu_total, vcpu_allocated,
            ram_gb_total, ram_gb_allocated
        )
        VALUES ($1,'k8s',$2,'ready','spot',0,0,2,1,4,1)
        RETURNING id
        """,
        pool_id,
        f"spot-{uuid.uuid4()}"
    )

    # ON-DEMAND node
    ond_node = await db.fetchval(
        """
        INSERT INTO compute_inventory (
            pool_id, provider, provider_instance_id,
            state, node_class,
            gpu_total, gpu_allocated,
            vcpu_total, vcpu_allocated,
            ram_gb_total, ram_gb_allocated
        )
        VALUES ($1,'k8s',$2,'ready','on_demand',0,0,2,1,4,1)
        RETURNING id
        """,
        pool_id,
        f"ond-{uuid.uuid4()}"
    )

    spot_alloc = uuid.uuid4().hex
    ond_alloc = uuid.uuid4().hex
    await db.execute(
        """
        INSERT INTO allocations (
            allocation_id, node_id,
            gpu, vcpu, ram_gb,
            priority, preemptible,
            node_class,
            owner_type, owner_id
        )
        VALUES
          ($1,$2,0,1,1,10,TRUE,'spot','user','spot-user'),
          ($3,$4,0,1,1,10,TRUE,'on_demand','user','ond-user')
        """,
        spot_alloc, spot_node,
        ond_alloc, ond_node
    )

    await reclaimer.reclaim()

    spot_left = await db.fetchval(
        "SELECT COUNT(*) FROM allocations WHERE allocation_id=$1",
        spot_alloc
    )
    ond_left = await db.fetchval(
        "SELECT COUNT(*) FROM allocations WHERE allocation_id=$1",
        ond_alloc
    )

    assert spot_left == 0
    assert ond_left == 1

    print("✔ Spot reclaimed, on-demand preserved")


if __name__ == "__main__":
    asyncio.run(test_spot_vs_ondemand())

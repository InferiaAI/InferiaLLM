import asyncio
import json
import uuid

from services.autoscaler.worker import Autoscaler
from repositories.autoscaler_repo import AutoscalerRepository
from services.adapter_stub import FakeAdapterStub
from infra.db import create_test_db_pool

async def test_autoscaler_scale_down():
    """
    GIVEN:
      - 2 READY nodes
      - 1 idle node
      - utilization below threshold
      - min_nodes = 1

    EXPECT:
      - exactly 1 node marked draining
      - DeprovisionNode called once
    """
    db_pool = await create_test_db_pool()

    autoscaler_repo = AutoscalerRepository(db_pool)
    adapter_stub = FakeAdapterStub()

    autoscaler = Autoscaler(autoscaler_repo, adapter_stub)

    # --------------------------------------------------
    # Create pool
    # --------------------------------------------------

    pool_name = f"autoscale-test-{uuid.uuid4().hex[:6]}"

    pool_id = await db_pool.fetchval(
        """
        INSERT INTO compute_pools (
          pool_name,
          owner_type,
          owner_id,
          provider,
          scheduling_policy,
          autoscaling_policy
        )
        VALUES (
          $1,
          'system',
          'system',
          'k8s',
          '{"strategy":"best_fit"}',
          $2
        )
        RETURNING id
        """,
        pool_name,
        json.dumps({
            "enabled": True,
            "min_nodes": 1,
            "max_nodes": 3,
            "scale_up_threshold": 0.8,
            "scale_down_threshold": 0.2,
            "cooldown_seconds": 0
        })
    )


    # --------------------------------------------------
    # Insert 2 READY nodes, one idle
    # --------------------------------------------------
    # await db_pool.execute(
    #     """
    #     INSERT INTO compute_inventory (
    #       pool_id, provider, provider_instance_id,
    #       state,
    #       gpu_total, gpu_allocated,
    #       vcpu_total, vcpu_allocated,
    #       ram_gb_total, ram_gb_allocated
    #     )
    #     VALUES
    #       ($1, 'k8s', 'idle-node', 'ready', 0,0,1,0,1,0),
    #       ($1, 'k8s', 'busy-node', 'ready', 0,0,1,1,1,1)
    #     """,
    #     pool_id
    # )

    await db_pool.execute(
        """
        INSERT INTO compute_inventory (
          pool_id,
          provider,
          provider_instance_id,
          state,
          gpu_total, gpu_allocated,
          vcpu_total, vcpu_allocated,
          ram_gb_total, ram_gb_allocated
        )
        VALUES (
          $1,
          'k8s',
          'scale-up-node-2',
          'ready',
          0,0,
          1,0,
          1,0
        )
        """,
        pool_id
    )

    # --------------------------------------------------
    # Run autoscaler
    # --------------------------------------------------
    await autoscaler.tick()

    # --------------------------------------------------
    # Assertions
    # --------------------------------------------------
    draining = await db_pool.fetchval(
        "SELECT COUNT(*) FROM compute_inventory WHERE state='draining'"
    )

    assert draining == 1, "Expected exactly one node to be drained"
    assert len(adapter_stub.deprovision_calls) == 1, \
        "Expected exactly one deprovision call"

    print("✔ Autoscaler scale-down test passed")


if __name__ == "__main__":
    asyncio.run(test_autoscaler_scale_down())  # Replace None with actual db_pool if needed
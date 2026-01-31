import asyncio
import uuid
import json

from services.autoscaler.worker import Autoscaler
from repositories.autoscaler_repo import AutoscalerRepository
from services.adapter_stub import FakeAdapterStub
from infra.db import create_test_db_pool


async def test_autoscaler_scale_up():
    """
    GIVEN:
      - pool with autoscaling enabled
      - max_nodes = 2
      - consecutive allocation failures >= 3

    EXPECT:
      - autoscaler calls ProvisionNode exactly once
    """
    db_pool = await create_test_db_pool()

    autoscaler_repo = AutoscalerRepository(db_pool)
    adapter_stub = FakeAdapterStub()

    autoscaler = Autoscaler(autoscaler_repo, adapter_stub)

    # --------------------------------------------------
    # Create pool with autoscaling policy
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
            "max_nodes": 2,
            "scale_up_threshold": 0.8,
            "scale_down_threshold": 0.2,
            "cooldown_seconds": 0
        })
    )

    # --------------------------------------------------
    # Simulate allocation failures
    # --------------------------------------------------
    for _ in range(3):
        await autoscaler_repo.incr_failures(pool_id)

    # --------------------------------------------------
    # Run autoscaler tick
    # --------------------------------------------------
    await autoscaler.tick()

    # --------------------------------------------------
    # Assertions
    # --------------------------------------------------
    assert len(adapter_stub.provision_calls) == 1, \
        "Autoscaler did not scale up as expected"

    print("✔ Autoscaler scale-up test passed")

if __name__ == "__main__":
    asyncio.run(test_autoscaler_scale_up())
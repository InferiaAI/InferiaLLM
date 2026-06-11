import uuid
import json
import grpc

from orchestration.v1 import scheduler_pb2_grpc


def uid():
    return uuid.uuid4().hex[:8]


async def scheduler_stub():
    channel = grpc.aio.insecure_channel("localhost:50051")
    return scheduler_pb2_grpc.SchedulerStub(channel)


async def create_test_pool(db, provider="k8s"):
    pool_name = f"test-pool-{uid()}"
    pool_id = await db.fetchval(
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
          $2,
          '{"strategy":"best_fit"}',
          $3
        )
        RETURNING id
        """,
        pool_name,
        provider,
        json.dumps({
            "enabled": False,
            "min_nodes": 0,
            "max_nodes": 10,
            "scale_up_threshold": 0.8,
            "scale_down_threshold": 0.2,
            "cooldown_seconds": 0,
        }),
    )
    return pool_id


async def create_ready_node(db, pool_id, vcpu=2, ram_gb=4, gpu=0, provider="k8s"):
    node_id = await db.fetchval(
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
          $1, $2, $3,
          'ready',
          $4, 0,
          $5, 0,
          $6, 0
        )
        RETURNING id
        """,
        pool_id,
        provider,
        f"test-node-{uid()}",
        gpu,
        vcpu,
        ram_gb,
    )
    return node_id

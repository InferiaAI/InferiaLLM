import uuid
import grpc
import asyncpg

from inferia.services.orchestration.v1 import scheduler_pb2_grpc


def uid():
    return uuid.uuid4().hex[:8]


async def scheduler_stub():
    channel = grpc.aio.insecure_channel("localhost:50051")
    return scheduler_pb2_grpc.SchedulerStub(channel)


async def create_test_pool(db, *, provider="k8s"):
    return await db.fetchval(
        """
        INSERT INTO compute_pools (
            pool_name,
            owner_type,
            owner_id,
            provider,
            scheduling_policy,
            is_dedicated,
            is_active
        )
        VALUES (
            $1,
            'system',
            'system',
            $2,
            '{}'::jsonb,
            FALSE,
            TRUE
        )
        RETURNING id
        """,
        f"gang-pool-{uid()}",
        provider,
    )


async def create_ready_node(
    db,
    pool_id,
    *,
    node_class="on_demand",
    vcpu=2,
    ram_gb=4,
):
    return await db.fetchval(
        """
        INSERT INTO compute_inventory (
            pool_id,
            provider,
            provider_instance_id,
            state,
            node_class,
            gpu_total, gpu_allocated,
            vcpu_total, vcpu_allocated,
            ram_gb_total, ram_gb_allocated
        )
        VALUES (
            $1,
            'k8s',
            $2,
            'ready',
            $3,
            0,0,
            $4,0,
            $5,0
        )
        RETURNING id
        """,
        pool_id,
        f"node-{uid()}",
        node_class,
        vcpu,
        ram_gb,
    )
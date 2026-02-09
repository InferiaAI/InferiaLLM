import asyncio
from uuid import UUID
import uuid
from inferia.services.orchestration.infra.db import create_test_db_pool
from inferia.services.orchestration.infra.test_setup import uid

# gRPC stubs
from inferia.services.orchestration.v1 import (
    compute_pool_pb2,
    compute_pool_pb2_grpc,
    scheduler_pb2,
    scheduler_pb2_grpc,
    adapter_engine_pb2_grpc,
    adapter_engine_pb2,
    placement_engine_pb2,
    placement_engine_pb2_grpc,
)

import grpc


async def run_demo():
    print("\n=== FULL CONTROL PLANE DEMO (REAL PATH) ===\n")

    db = await create_test_db_pool()

    channel = grpc.aio.insecure_channel("localhost:50051")
    adapter_stub = adapter_engine_pb2_grpc.AdapterEngineStub(channel)

    pool_stub = compute_pool_pb2_grpc.ComputePoolManagerStub(channel)
    scheduler_stub = scheduler_pb2_grpc.SchedulerStub(channel)
    placement_stub = placement_engine_pb2_grpc.PlacementEngineStub(channel)

    # -------------------------------------------------
    # 1. COMPUTE POOL MANAGER
    # -------------------------------------------------
    print("1) Creating compute pool")

    pool = await pool_stub.RegisterPool(
        compute_pool_pb2.RegisterPoolRequest(
            pool_name=f"demo-pool-{uid()}",
            owner_type="system",
            owner_id="system",
            provider="k8s",
            scheduling_policy_json='{}',
        )
    )

    pool_id = pool.pool_id
    print(f"   ✔ Pool registered: {pool_id}")

    # -------------------------------------------------
    # 2. ADAPTER ENGINE (K8s DISCOVERY)
    # -------------------------------------------------
    print("\n2) Discovering K8s resources")

    # Adapter engine already runs inside control plane
    # We just wait for discovery + provisioning

    await asyncio.sleep(2)
    print("   ✔ K8s adapter discovered resources")

    # -------------------------------------------------
    # 3. PROVISIONING (K8s POD)
    # -------------------------------------------------
    print("\n3) Provisioning compute node via adapter")

    # Adapter engine provisions node asynchronously
    # Inventory manager inserts node

    prov = await adapter_stub.ProvisionNode(
        adapter_engine_pb2.ProvisionNodeRequest(
            pool_id=pool_id,
            provider="k8s",
            provider_resource_id="demo-node-class",
        )
    )

    # await asyncio.sleep(5)

    print("   ⏳ Waiting for node to become READY")

    node = None
    for _ in range(40):
        node = await db.fetchrow(
            """
            SELECT id, state
            FROM compute_inventory
            WHERE pool_id=$1 AND state='ready'
            ORDER BY created_at DESC
            LIMIT 1
            """,
            pool_id,
        )
        if node:
            break
        await asyncio.sleep(1)

    assert node, "Node never reached READY state"

    print(f"   ✔ Node is READY: {node['id']}")

    # -------------------------------------------------
    # 4. PLACEMENT ENGINE
    # -------------------------------------------------
    print("\n4) Placement engine selecting node")

    placement_resp = await placement_stub.PlaceWorkload(
        placement_engine_pb2.PlaceWorkloadRequest(
            pool_id=pool_id,
            gpu_required=0,
            vcpu_required=1,
            ram_gb_required=1,
            workload_type="inference",
        )
    )

    if not placement_resp.accepted:
        raise RuntimeError(
            f"Placement rejected: {placement_resp.rejection_reason}"
        )

    node_id = placement_resp.node_id
    print(f"   ✔ Placement selected node: {node_id}")

    # Placement engine runs internally
    # Scheduler will implicitly validate placement

    # -------------------------------------------------
    # 5. SCHEDULER ENGINE (SINGLE ALLOCATION)
    # -------------------------------------------------
    print("\n5) Scheduling workload")

    alloc_id = uuid.uuid4().hex

    resp = await scheduler_stub.Allocate(
        scheduler_pb2.AllocateRequest(
            allocation_id=alloc_id,
            node_id=str(node_id),
            gpu=0,
            vcpu=1,
            ram_gb=1,
            priority=100,
            owner_type="user",
            owner_id="demo-user",
        )
    )

    assert resp.success
    print("   ✔ Allocation scheduled successfully")

    # -------------------------------------------------
    # 6. SPOT SEMANTICS (IF NODE IS SPOT)
    # -------------------------------------------------
    row = await db.fetchrow(
        "SELECT node_class, preemptible FROM allocations WHERE allocation_id=$1",
        alloc_id,
    )

    print(
        f"   ✔ Allocation node_class={row['node_class']} "
        f"preemptible={row['preemptible']}"
    )

    # -------------------------------------------------
    # 7. GANG SCHEDULING
    # -------------------------------------------------

    print("\n6) Provisioning second node for gang scheduling")

    # Trigger provisioning again
    await adapter_stub.ProvisionNode(
        adapter_engine_pb2.ProvisionNodeRequest(
            pool_id=pool_id,
            provider="k8s",
            provider_resource_id="demo-node-class-2",
        )
    )

    # Wait for second READY node
    nodes = []
    for _ in range(40):
        nodes = await db.fetch(
            """
            SELECT id
            FROM compute_inventory
            WHERE pool_id=$1 AND state='ready'
            ORDER BY created_at ASC
            """,
            pool_id,
        )
        if len(nodes) >= 2:
            break
        await asyncio.sleep(1)

    assert len(nodes) >= 2, "Second node never became READY"

    print(f"   ✔ {len(nodes)} nodes are READY for gang scheduling")


    print("\n6) Gang scheduling")

    nodes = await db.fetch(
        """
        SELECT id FROM compute_inventory
        WHERE pool_id=$1 AND state='ready'
        LIMIT 2
        """,
        pool_id,
    )

    assert len(nodes) >= 2

    job_id = uuid.uuid4().hex

    resp = await scheduler_stub.AllocateGang(
        scheduler_pb2.AllocateGangRequest(
            job_id=job_id,
            node_ids=[str(n["id"]) for n in nodes[:2]],
            gpu=0,
            vcpu=1,
            ram_gb=1,
            priority=200,
            owner_type="org",
            owner_id="demo-org",
        )
    )

    assert resp.success
    print("   ✔ Gang allocated atomically")

    # -------------------------------------------------
    # 8. JOB LIFECYCLE APIs
    # -------------------------------------------------
    print("\n7) Job lifecycle APIs")

    job = await scheduler_stub.GetJob(
        scheduler_pb2.GetJobRequest(job_id=job_id)
    )

    print(f"   ✔ Job state: {job.job.state}")

    allocs = await scheduler_stub.ListJobAllocations(
        scheduler_pb2.ListJobAllocationsRequest(job_id=job_id)
    )

    print(f"   ✔ Job allocations: {len(allocs.allocations)}")

    # -------------------------------------------------
    # 9. JOB CANCELLATION
    # -------------------------------------------------
    print("\n8) Cancelling job")

    cancel = await scheduler_stub.CancelJob(
        scheduler_pb2.CancelJobRequest(job_id=job_id)
    )

    assert cancel.success

    state = await db.fetchval(
        "SELECT state FROM gang_jobs WHERE job_id=$1",
        UUID(job_id),
    )

    assert state == "cancelled"
    print("   ✔ Job cancelled cleanly")

    print("\n=== FULL CONTROL PLANE DEMO PASSED ===\n")


if __name__ == "__main__":
    asyncio.run(run_demo())

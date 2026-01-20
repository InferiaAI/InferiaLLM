import asyncio
import grpc
import uuid
import time

from v1 import (
    compute_pool_pb2,
    compute_pool_pb2_grpc,
    adapter_engine_pb2,
    adapter_engine_pb2_grpc,
    inventory_manager_pb2,
    inventory_manager_pb2_grpc,
    placement_engine_pb2,
    placement_engine_pb2_grpc,
)

GRPC_ENDPOINT = "localhost:50051"


async def run_e2e_test():
    async with grpc.aio.insecure_channel(GRPC_ENDPOINT) as channel:

        # ---------------------------------------------------------
        # Stubs
        # ---------------------------------------------------------
        pool_stub = compute_pool_pb2_grpc.ComputePoolManagerStub(channel)
        adapter_stub = adapter_engine_pb2_grpc.AdapterEngineStub(channel)
        inventory_stub = inventory_manager_pb2_grpc.InventoryManagerStub(channel)
        placement_stub = placement_engine_pb2_grpc.PlacementEngineStub(channel)

        print("\n=== 1. CREATE COMPUTE POOL ===")

        pool_name = f"test-pool-{uuid.uuid4().hex[:6]}"

        pool_resp = await pool_stub.RegisterPool(
            compute_pool_pb2.RegisterPoolRequest(
                pool_name=pool_name,
                owner_type="system",
                owner_id="system",
                provider="nosana",
                allowed_gpu_types=["A10G"],
                max_cost_per_hour=5.0,
                is_dedicated=False,
                scheduling_policy_json='{"strategy":"best_fit"}',
            )
        )

        pool_id = pool_resp.pool_id
        print("✔ Pool created:", pool_id)

        # ---------------------------------------------------------
        # 2. DISCOVER PROVIDER RESOURCES
        # ---------------------------------------------------------
        print("\n=== 2. DISCOVER PROVIDER RESOURCES ===")

        await adapter_stub.DiscoverProviderResources(
            adapter_engine_pb2.DiscoverRequest(provider="nosana")
        )

        print("✔ Provider resources discovered")

        # ---------------------------------------------------------
        # 3. PROVISION NODE
        # ---------------------------------------------------------
        print("\n=== 3. PROVISION NODE ===")

        provision_resp = await adapter_stub.ProvisionNode(
            adapter_engine_pb2.ProvisionNodeRequest(
                provider="nosana",
                provider_resource_id="nosana-rtx3090",
                pool_id=pool_id,
            )
        )

        node_id = provision_resp.node_id
        print("✔ Node provisioned:", node_id)

        # ---------------------------------------------------------
        # 4. REGISTER NODE WITH INVENTORY
        # ---------------------------------------------------------
        print("\n=== 4. REGISTER NODE IN INVENTORY ===")

        provider_instance_id = "i-test-" + uuid.uuid4().hex[:6]

        await inventory_stub.InvenRegisterNode(
            inventory_manager_pb2.InvenRegisterNodeRequest(
                pool_id=pool_id,
                provider="nosana",
                provider_instance_id=provider_instance_id,
                hostname="test-node",
                gpu_total=1,
                vcpu_total=8,
                ram_gb_total=32,
            )
        )

        print("✔ Node registered in inventory")

        # ---------------------------------------------------------
        # 5. SEND HEARTBEAT (TRANSITION TO READY)
        # ---------------------------------------------------------
        print("\n=== 5. SEND HEARTBEAT ===")

        await inventory_stub.InvenHeartbeat(
            inventory_manager_pb2.InvenHeartbeatRequest(
                provider="nosana",
                provider_instance_id=provider_instance_id,
                gpu_allocated=0,
                vcpu_allocated=0,
                ram_gb_allocated=0,
                health_score=100,
            )
        )

        print("✔ Heartbeat sent, node should be READY")

        # Small delay to ensure DB commit
        await asyncio.sleep(1)

        # ---------------------------------------------------------
        # 6. PLACEMENT (SUCCESS CASE)
        # ---------------------------------------------------------
        print("\n=== 6. PLACEMENT (VALID REQUEST) ===")

        placement_resp = await placement_stub.PlaceWorkload(
            placement_engine_pb2.PlaceWorkloadRequest(
                pool_id=pool_id,
                gpu_required=1,
                vcpu_required=2,
                ram_gb_required=4,
                workload_type="inference",
            )
        )

        if not placement_resp.accepted:
            raise RuntimeError(
                f"Placement failed unexpectedly: {placement_resp.rejection_reason}"
            )

        print("✔ Placement accepted on node:", placement_resp.node_id)

        # ---------------------------------------------------------
        # 7. PLACEMENT (REJECTION CASE)
        # ---------------------------------------------------------
        print("\n=== 7. PLACEMENT (INVALID REQUEST) ===")

        reject_resp = await placement_stub.PlaceWorkload(
            placement_engine_pb2.PlaceWorkloadRequest(
                pool_id=pool_id,
                gpu_required=8,
                vcpu_required=64,
                ram_gb_required=256,
                workload_type="training",
            )
        )

        if reject_resp.accepted:
            raise RuntimeError("Invalid placement was incorrectly accepted")

        print("✔ Correctly rejected:", reject_resp.rejection_reason)

        print("\n=== ✅ END-TO-END TEST PASSED ===\n")


if __name__ == "__main__":
    asyncio.run(run_e2e_test())

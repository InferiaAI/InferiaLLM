import asyncio
import uuid
import grpc
import time

from v1 import (
    compute_pool_pb2,
    compute_pool_pb2_grpc,
    adapter_engine_pb2,
    adapter_engine_pb2_grpc,
    placement_engine_pb2,
    placement_engine_pb2_grpc,
)

GRPC_ENDPOINT = "localhost:50051"


async def wait_for_ready_placement(
    placement_stub, pool_id, timeout=240
):
    """
    Poll placement until a node becomes schedulable.
    This waits for k8s liveness → inventory readiness.
    """
    start = time.time()

    while time.time() - start < timeout:
        resp = await placement_stub.PlaceWorkload(
            placement_engine_pb2.PlaceWorkloadRequest(
                pool_id=pool_id,
                gpu_required=0,
                vcpu_required=1,
                ram_gb_required=1,
                workload_type="test",
            )
        )

        if resp.accepted:
            return resp.node_id

        await asyncio.sleep(3)

    raise TimeoutError("No READY k8s node appeared in inventory")


async def run_k8s_e2e_test():
    async with grpc.aio.insecure_channel(GRPC_ENDPOINT) as channel:

        pool_stub = compute_pool_pb2_grpc.ComputePoolManagerStub(channel)
        adapter_stub = adapter_engine_pb2_grpc.AdapterEngineStub(channel)
        placement_stub = placement_engine_pb2_grpc.PlacementEngineStub(channel)

        print("\n=== 1. CREATE K8S COMPUTE POOL ===")

        pool_name = f"k8s-pool-{uuid.uuid4().hex[:6]}"

        pool_resp = await pool_stub.RegisterPool(
            compute_pool_pb2.RegisterPoolRequest(
                pool_name=pool_name,
                owner_type="system",
                owner_id="system",
                provider="k8s",
                allowed_gpu_types=[],
                max_cost_per_hour=0.0,
                is_dedicated=False,
                scheduling_policy_json='{"strategy":"best_fit"}',
            )
        )

        pool_id = pool_resp.pool_id
        print("✔ Pool created:", pool_id)

        # ---------------------------------------------------------
        # 2. DISCOVER K8S RESOURCES
        # ---------------------------------------------------------
        print("\n=== 2. DISCOVER K8S RESOURCES ===")

        await adapter_stub.DiscoverProviderResources(
            adapter_engine_pb2.DiscoverRequest(provider="k8s")
        )

        print("✔ K8s resources discovered")

        # ---------------------------------------------------------
        # 3. PROVISION K8S POD (COMPUTE NODE)
        # ---------------------------------------------------------
        print("\n=== 3. PROVISION K8S POD ===")

        provision_resp = await adapter_stub.ProvisionNode(
            adapter_engine_pb2.ProvisionNodeRequest(
                provider="k8s",
                provider_resource_id="k8s-any",
                pool_id=pool_id,
            )
        )

        print("✔ Pod provisioned (compute node)")
        print("Node ID:", provision_resp.node_id)

        # ---------------------------------------------------------
        # 4. WAIT FOR INVENTORY → READY
        # ---------------------------------------------------------
        print("\n=== 4. WAIT FOR K8S LIVENESS ===")
        print("Waiting for pod to reach READY state...")

        node_id = await wait_for_ready_placement(
            placement_stub, pool_id
        )

        print("✔ Node is READY and schedulable:", node_id)

        # ---------------------------------------------------------
        # 5. VALID PLACEMENT
        # ---------------------------------------------------------
        print("\n=== 5. VALID PLACEMENT REQUEST ===")

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
                f"Placement unexpectedly failed: "
                f"{placement_resp.rejection_reason}"
            )

        print("✔ Placement accepted on node:", placement_resp.node_id)

        # ---------------------------------------------------------
        # 6. INVALID PLACEMENT
        # ---------------------------------------------------------
        print("\n=== 6. INVALID PLACEMENT REQUEST ===")

        reject_resp = await placement_stub.PlaceWorkload(
            placement_engine_pb2.PlaceWorkloadRequest(
                pool_id=pool_id,
                gpu_required=10,
                vcpu_required=64,
                ram_gb_required=512,
                workload_type="training",
            )
        )

        if reject_resp.accepted:
            raise RuntimeError("Invalid placement was incorrectly accepted")

        print("✔ Correctly rejected:", reject_resp.rejection_reason)

        print("\n=== ✅ K8S END-TO-END TEST PASSED ===\n")


if __name__ == "__main__":
    asyncio.run(run_k8s_e2e_test())

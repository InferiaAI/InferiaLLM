import asyncio
import grpc
from inferia.services.orchestration.v1 import (
    placement_engine_pb2,
    placement_engine_pb2_grpc,
)

GRPC_ENDPOINT = "localhost:50051"

# MUST be an existing pool_id
POOL_ID = "17d39104-e42d-4d57-9677-a19392a53a8e"


async def test_successful_placement():
    async with grpc.aio.insecure_channel(GRPC_ENDPOINT) as channel:
        stub = placement_engine_pb2_grpc.PlacementEngineStub(channel)

        print("==> Requesting valid placement")

        response = await stub.PlaceWorkload(
            placement_engine_pb2.PlaceWorkloadRequest(
                pool_id=POOL_ID,
                gpu_required=1,
                vcpu_required=2,
                ram_gb_required=4,
                workload_type="inference",
            )
        )

        if response.accepted:
            print("✔ Placement accepted")
            print("Selected node_id:", response.node_id)
        else:
            print("✖ Placement rejected:", response.rejection_reason)


async def test_rejected_placement():
    async with grpc.aio.insecure_channel(GRPC_ENDPOINT) as channel:
        stub = placement_engine_pb2_grpc.PlacementEngineStub(channel)

        print("\n==> Requesting impossible placement")

        response = await stub.PlaceWorkload(
            placement_engine_pb2.PlaceWorkloadRequest(
                pool_id=POOL_ID,
                gpu_required=4,   # more than gpu_total
                vcpu_required=64,
                ram_gb_required=256,
                workload_type="training",
            )
        )

        if not response.accepted:
            print("✔ Correctly rejected")
            print("Reason:", response.rejection_reason)
        else:
            print("✖ Unexpected placement:", response.node_id)


async def main():
    await test_successful_placement()
    await test_rejected_placement()


if __name__ == "__main__":
    asyncio.run(main())

import asyncio
from grpc.experimental import aio
from inferia.services.orchestration.v1 import compute_pool_pb2, compute_pool_pb2_grpc
from inferia.services.orchestration.v1 import compute_node_pb2, compute_node_pb2_grpc


async def test():
    async with aio.insecure_channel("localhost:50051") as channel:
        stub = compute_pool_pb2_grpc.ComputePoolServiceStub(channel)

        pool = await stub.RegisterPool(
            compute_pool_pb2.RegisterPoolRequest(
                name="test-pool",
                provider="nosana",
                region="eu-west",
            )
        )

        print("Created pool:", pool)

        pools = await stub.ListPools(None)
        print("All pools:", pools)


asyncio.run(test())
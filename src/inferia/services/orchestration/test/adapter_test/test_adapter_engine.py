import asyncio
import grpc
from inferia.services.orchestration.v1 import adapter_engine_pb2, adapter_engine_pb2_grpc


GRPC_ENDPOINT = "localhost:50051"

# Replace this with an existing pool_id from your DB
POOL_ID = "2afc272c-9a8a-4cf7-b0cb-1e0720e972ea"

async def test_adapter_engine():
    async with grpc.aio.insecure_channel(GRPC_ENDPOINT) as channel:
        stub = adapter_engine_pb2_grpc.AdapterEngineStub(channel)

        print("==> Discovering Nosana provider resources")
        await stub.DiscoverProviderResources(
            adapter_engine_pb2.DiscoverRequest(provider="nosana")
        )
        print("✔ Provider resources discovered")

        print("\n==> Provisioning node from Nosana adapter")
        response = await stub.ProvisionNode(
            adapter_engine_pb2.ProvisionNodeRequest(
                provider="nosana",
                provider_resource_id="nosana-rtx3090", # Using a known key from simulation mode
                pool_id=POOL_ID,
            )
        )

        print("✔ Node provisioned")
        print("Node ID:", response.node_id)


if __name__ == "__main__":
    asyncio.run(test_adapter_engine())

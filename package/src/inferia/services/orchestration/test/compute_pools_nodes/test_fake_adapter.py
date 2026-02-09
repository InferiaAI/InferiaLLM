import asyncio
import grpc

from inferia.services.orchestration.services.adapters.base.adapter import ProviderAdapter
from inferia.services.orchestration.v1 import compute_node_pb2, compute_node_pb2_grpc


class FakeAdapter(ProviderAdapter):
    async def discover_nodes(self):
        return [
            {
                "node_id": "fake-node-1",
                "pool_id": "b3673f36-2cf1-49df-bf44-a7c6b6867839",
                "node_name": "fake-node",
                "node_type": "gpu-node",
                "allocatable": {
                    "cpu": "8",
                    "memory": "32Gi",
                    "gpu": "1",
                },
            }
        ]

    async def get_node_metadata(self, node_id):
        return {}

    async def reconcile(self):
        pass


async def test_adapter():
    channel = grpc.aio.insecure_channel("localhost:50051")
    stub = compute_node_pb2_grpc.ComputeNodeServiceStub(channel)

    adapter = FakeAdapter()
    nodes = await adapter.discover_nodes()

    for n in nodes:
        print("Registering node:", n["node_id"])
        await stub.RegisterNode(
            compute_node_pb2.RegisterNodeRequest(
                pool_id=n["pool_id"],
                node_name=n["node_name"],
                node_type=n["node_type"],
                allocatable=n["allocatable"],
            )
        )

    print("Fake adapter test passed")


if __name__ == "__main__":
    asyncio.run(test_adapter())

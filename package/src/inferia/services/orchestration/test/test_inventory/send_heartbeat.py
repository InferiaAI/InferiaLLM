import asyncio
import grpc

from inferia.services.orchestration.v1 import (
    inventory_manager_pb2,
    inventory_manager_pb2_grpc,
)

GRPC_ENDPOINT = "localhost:50051"

# PUT VALUES FROM DB HERE
PROVIDER = "k8s"
PROVIDER_INSTANCE_ID = "inferia-worker-1d6d98"

async def send_heartbeat():
    async with grpc.aio.insecure_channel(GRPC_ENDPOINT) as channel:
        stub = inventory_manager_pb2_grpc.InventoryManagerStub(channel)

        print("Sending heartbeat...")

        await stub.InvenHeartbeat(
            inventory_manager_pb2.InvenHeartbeatRequest(
                provider=PROVIDER,
                provider_instance_id=PROVIDER_INSTANCE_ID,
                gpu_allocated=0,
                vcpu_allocated=0,
                ram_gb_allocated=0,
                health_score=100,
            )
        )

        print("✔ Heartbeat sent")

if __name__ == "__main__":
    asyncio.run(send_heartbeat())

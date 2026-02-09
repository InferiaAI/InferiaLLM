import asyncio
import grpc
import time
import json
import redis.asyncio as redis

from inferia.services.orchestration.v1 import compute_node_pb2, compute_node_pb2_grpc


GRPC_ADDR = "localhost:50051"
REDIS_URL = "redis://localhost:6379"

# Use an existing node_id from DB
NODE_ID = "146c77ed-c8ef-474e-9e22-34051d507d3a"


async def test_heartbeat():
    # gRPC client
    channel = grpc.aio.insecure_channel(GRPC_ADDR)
    stub = compute_node_pb2_grpc.ComputeNodeServiceStub(channel)

    # Redis client
    r = redis.from_url(REDIS_URL, decode_responses=True)

    used_payload = {
        "cpu": "6",
        "memory": "20480",
        "gpu": "1",
    }

    print("Sending heartbeat...")
    await stub.Heartbeat(
        compute_node_pb2.NodeHeartbeatRequest(
            node_id=NODE_ID,
            used=used_payload,
        )
    )

    # Give Redis a moment
    await asyncio.sleep(1)

    redis_key = f"node:{NODE_ID}"
    state = await r.hgetall(redis_key)

    print("\nRedis Node State:")
    print(json.dumps(state, indent=2))

    assert state, "Redis state missing"
    assert "used" in state, "Used resources missing"
    assert json.loads(state["used"])["gpu"] == "1"

    print("\nHeartbeat test passed")

if __name__ == "__main__":
    asyncio.run(test_heartbeat())

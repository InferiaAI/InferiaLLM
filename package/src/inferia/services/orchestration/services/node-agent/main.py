import asyncio
from agent.cpu import get_cpu_info
from agent.memory import get_memory_info
from agent.gpu import get_gpu_info
from agent.heartbeat import HeartbeatClient


import os

NODE_ID = os.getenv("NODE_ID", "node-123")
GRPC_ADDR = os.getenv("ORCHESTRATOR_GRPC_ADDR", "localhost:50051")


async def run():
    client = HeartbeatClient(GRPC_ADDR, NODE_ID)

    while True:
        cpu = get_cpu_info()
        mem = get_memory_info()
        gpu = get_gpu_info()

        used = {
            "cpu": str(cpu["cpu_used"]),
            "memory": str(mem["memory_used"]),
            "gpu": str(len(gpu)),
        }

        await client.send(used)
        await asyncio.sleep(5)


if __name__ == "__main__":
    asyncio.run(run())

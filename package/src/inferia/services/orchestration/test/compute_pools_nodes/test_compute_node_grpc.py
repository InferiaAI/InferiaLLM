import asyncio
import grpc
import json
import datetime as dt

from inferia.services.orchestration.v1 import (
    compute_pool_pb2,
    compute_pool_pb2_grpc,
)


GRPC_ADDR = "localhost:50051"


async def test_compute_node():
    async with grpc.aio.insecure_channel(GRPC_ADDR) as channel:

        # -----------------------------
        # Clients
        # -----------------------------
        pool_stub = compute_pool_pb2_grpc.ComputePoolManagerStub(channel)        

        # -----------------------------
        # 1. Register Compute Pool
        # -----------------------------
        pool_resp = await pool_stub.RegisterPool(
            compute_pool_pb2.RegisterPoolRequest(
                pool_name="test-pool1",
                owner_type="user",
                owner_id="user-123332sssss",
                provider="nosana",
                allowed_gpu_types=["A100"],
                max_cost_per_hour=10.0,
                is_dedicated=False,
            )
        )

        print("\nCompute Pool Registered")
        print(pool_resp)

        # pool_id = pool_resp.id

        # -----------------------------
        # 2. Register Compute Node
        # -----------------------------
        # node_resp = await node_stub.RegisterNode(
        #     compute_node_pb2.RegisterNodeRequest(
        #         pool_id=pool_id,
        #         node_name="node-1",
        #         node_type="gpu-node",
        #         allocatable={
        #             "cpu": "16",
        #             "memory": "64Gi",
        #             "gpu": "1",
        #             "gpu_type": "A100",
        #         },
        #     )
        # )

        # print("\nCompute Node Registered")
        # print(node_resp)

        # node_id = node_resp.id

        # # -----------------------------
        # # 3. Send Heartbeat
        # # -----------------------------
        # await node_stub.Heartbeat(
        #     compute_node_pb2.NodeHeartbeatRequest(
        #         node_id=node_id,
        #         used={
        #             "cpu": "4",
        #             "memory": "12Gi",
        #             "gpu": "1",
        #         },
        #     )
        # )

        print("\nHeartbeat Sent")

        # -----------------------------
        # 4. List Nodes for Pool
        # -----------------------------
        # nodes_resp = await node_stub.ListNodes(
        #     compute_node_pb2.ListNodesRequest(
        #         pool_id=pool_id
        #     )
        # )

        # print("\nNodes in Pool:")
        # for n in nodes_resp.nodes:
        #     print(n)

        pool_list = await pool_stub.ListPools(
            compute_pool_pb2.ListPoolsRequest(
                owner_id="user-123332ssss"
            )
        )

        print("\nPools for Owner:")
        for p in pool_list.pools:
            print(p)


if __name__ == "__main__":
    asyncio.run(test_compute_node())

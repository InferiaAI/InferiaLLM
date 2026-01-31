import grpc
from v1 import compute_node_pb2, compute_node_pb2_grpc


class HeartbeatClient:
    def __init__(self, server_addr: str, node_id: str):
        self.node_id = node_id
        self.channel = grpc.aio.insecure_channel(server_addr)
        self.stub = compute_node_pb2_grpc.ComputeNodeServiceStub(self.channel)

    async def send(self, used: dict):
        await self.stub.Heartbeat(
            compute_node_pb2.NodeHeartbeatRequest(
                node_id=self.node_id,
                used=used,
            )
        )

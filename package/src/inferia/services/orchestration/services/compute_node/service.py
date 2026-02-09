from datetime import datetime, timezone
from uuid import UUID

from inferia.services.orchestration.v1 import compute_node_pb2, compute_node_pb2_grpc


class ComputeNodeService(
    compute_node_pb2_grpc.ComputeNodeServiceServicer
):
    def __init__(self, inventory_repo):
        self.inventory = inventory_repo

    async def Heartbeat(self, request, context):
        def utcnow_naive():
            return datetime.now(timezone.utc).replace(tzinfo=None)

        node_id = UUID(request.node_id)
        used = request.used

        node = await self.inventory.get(node_id)
        if not node:
            context.abort(5, "Node not found")

        now = utcnow_naive()

        # --------------------------------------------------
        # CRITICAL STATE TRANSITION (MISSING IN YOUR SYSTEM)
        # --------------------------------------------------
        if node["state"] == "provisioning":
            await self.inventory.mark_ready(
                node_id=node_id,
                last_heartbeat=now,
            )
        else:
            await self.inventory.update_heartbeat(
                node_id=node_id,
                last_heartbeat=now,
            )

        # Optional: update resource usage
        await self.inventory.update_usage(
            node_id=node_id,
            used=used,
        )

        return compute_node_pb2.NodeHeartbeatResponse(
            accepted=True
        )

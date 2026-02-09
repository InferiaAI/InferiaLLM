from uuid import UUID
from datetime import datetime, timezone

from inferia.services.orchestration.v1 import (
    inventory_manager_pb2,
    inventory_manager_pb2_grpc,
)


class InventoryManagerService(
    inventory_manager_pb2_grpc.InventoryManagerServicer
):
    def __init__(self, repo, event_bus):
        self.repo = repo
        self.event_bus = event_bus

    # -------------------------------------------------
    # REGISTER NODE (CALLED BY ADAPTER / BOOTSTRAP)
    # -------------------------------------------------
    async def InvenRegisterNode(self, request, context):
        node_id = await self.repo.inven_register_node(
            pool_id=UUID(request.pool_id),
            provider=request.provider,
            provider_instance_id=request.provider_instance_id,
            hostname=request.hostname,
            gpu_total=request.gpu_total,
            vcpu_total=request.vcpu_total,
            ram_gb_total=request.ram_gb_total,
            state="provisioning",
        )

        await self.event_bus.publish(
            "node.registered",
            {
                "event_version": 1,
                "node_id": str(node_id),
                "pool_id": request.pool_id,
                "provider": request.provider,
            },
        )

        return inventory_manager_pb2.InvenRegisterNodeResponse(
            node_id=str(node_id)
        )

    # -------------------------------------------------
    # HEARTBEAT (AUTHORITATIVE STATE RECONCILER)
    # -------------------------------------------------
    async def InvenHeartbeat(self, request, context):
        def utcnow_naive():
            return datetime.now(timezone.utc).replace(tzinfo=None)

        node_id = UUID(request.node_id)
        now = utcnow_naive()

        node = await self.repo.get(node_id)
        if not node:
            context.abort(5, "Node not found")

        # ---------- STATE TRANSITION ----------
        if node["state"] == "provisioning":
            await self.repo.mark_ready(
                node_id=node_id,
                last_heartbeat=now,
            )

            await self.event_bus.publish(
                "node.ready",
                {
                    "event_version": 1,
                    "node_id": request.node_id,
                    "pool_id": str(node["pool_id"]),
                },
            )
        else:
            await self.repo.update_heartbeat(
                node_id=node_id,
                last_heartbeat=now,
            )

        # ---------- USAGE UPDATE ----------
        await self.repo.update_usage(
            node_id=node_id,
            gpu_allocated=request.gpu_allocated,
            vcpu_allocated=request.vcpu_allocated,
            ram_gb_allocated=request.ram_gb_allocated,
            health_score=request.health_score or 100,
        )

        # ---------- ACTIVE TRIGGER ----------
        await self.event_bus.publish(
            "node.heartbeat_received",
            {
                "event_version": 1,
                "node_id": request.node_id,
                "pool_id": str(node["pool_id"]),
            },
        )

        return inventory_manager_pb2.InvenEmpty()

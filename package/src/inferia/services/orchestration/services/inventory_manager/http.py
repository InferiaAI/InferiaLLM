from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
import os
from inferia.services.orchestration.repositories.inventory_repo import (
    InventoryRepository,
)
from uuid import UUID

FILTRATION_DATABASE_URL = os.getenv("FILTRATION_DATABASE_URL")

router = APIRouter(prefix="/inventory", tags=["Inventory"])


class HeartbeatPayload(BaseModel):
    provider: str
    provider_instance_id: str
    gpu_allocated: int = Field(default=0, ge=0)
    vcpu_allocated: int = Field(default=0, ge=0)
    ram_gb_allocated: int = Field(default=0, ge=0)
    health_score: int = Field(default=100, ge=0, le=100)
    state: str = "READY"
    expose_url: str | None = None


from inferia.services.orchestration.repositories.model_deployment_repo import (
    ModelDeploymentRepository,
)
from inferia.services.orchestration.infra.redis_event_bus import RedisEventBus
from inferia.services.orchestration.services.adapter_engine.registry import get_adapter

# Configuration for ephemeral provider failure detection
EPHEMERAL_FAILURE_THRESHOLD_MINUTES = int(
    os.getenv("EPHEMERAL_FAILURE_THRESHOLD_MINUTES", "10")
)


@router.post("/heartbeat")
async def heartbeat(payload: HeartbeatPayload, request: Request):
    db_pool = request.app.state.pool
    inventory_repo = InventoryRepository(db_pool)
    event_bus = RedisEventBus()
    deployment_repo = ModelDeploymentRepository(db_pool, event_bus)

    node = await inventory_repo.heartbeat(
        {
            "provider": payload.provider,
            "provider_instance_id": payload.provider_instance_id,
            "gpu_allocated": payload.gpu_allocated,
            "vcpu_allocated": payload.vcpu_allocated,
            "ram_gb_allocated": payload.ram_gb_allocated,
            "health_score": payload.health_score,
            "state": payload.state,
            "expose_url": payload.expose_url,
        }
    )

    if not node:
        raise HTTPException(status_code=404, detail="Node not found")

    # Sync with shared DB via main repo if expose_url is present
    if node.get("expose_url"):
        deployments = await inventory_repo.get_deployments_for_node(node["id"])

        if deployments:
            for d_id in deployments:
                await deployment_repo.update_endpoint(
                    deployment_id=d_id, endpoint=node["expose_url"]
                )

    # Sync Node State -> Deployment State
    # If the node is terminated or unhealthy, update the associated deployments
    if payload.state.lower() in ["terminated", "unhealthy", "failed"]:
        deployments = await inventory_repo.get_deployments_for_node(node["id"])
        target_state = (
            "TERMINATED" if payload.state.lower() == "terminated" else "FAILED"
        )

        for d_id in deployments:
            # Check current state to avoid overwriting user intent or loops
            current_d = await deployment_repo.get(d_id)
            if current_d and current_d["state"] not in [
                "TERMINATED",
                "FAILED",
                "STOPPED",
            ]:
                final_state = target_state

                # For ephemeral providers (DePIN, spot), check if failure was quick
                # Quick failures might indicate provider issues rather than user errors
                try:
                    adapter = get_adapter(payload.provider)
                    capabilities = adapter.get_capabilities()

                    if capabilities.is_ephemeral and target_state == "TERMINATED":
                        from datetime import datetime, timedelta, timezone

                        def utcnow_naive():
                            return datetime.now(timezone.utc).replace(tzinfo=None)

                        created_at = current_d.get("created_at")
                        if created_at:
                            if created_at.tzinfo is not None:
                                created_at = created_at.replace(tzinfo=None)

                            now = utcnow_naive()
                            duration = now - created_at
                            if duration < timedelta(
                                minutes=EPHEMERAL_FAILURE_THRESHOLD_MINUTES
                            ):
                                final_state = "FAILED"

                except Exception as e:
                    # If we can't determine ephemeral status, use default logic
                    pass

                # Enforce Sticky Deployment: TERMINATED -> STOPPED
                if final_state == "TERMINATED":
                    final_state = "STOPPED"

                await deployment_repo.update_state(d_id, final_state)

    return {"status": "ok"}


@router.get("/nodes/{provider}")
async def list_nodes(provider: str, request: Request):
    """List all nodes for a specific provider."""
    db_pool = request.app.state.pool
    inventory_repo = InventoryRepository(db_pool)

    try:
        nodes = await inventory_repo.list_nodes_by_provider(provider)
        return {"nodes": nodes}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to list nodes: {str(e)}")


@router.get("/providers")
async def list_providers():
    """List all registered providers and their capabilities."""
    from inferia.services.orchestration.services.adapter_engine.registry import (
        get_provider_info,
    )

    try:
        providers = get_provider_info()
        return {"providers": providers}
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Failed to list providers: {str(e)}"
        )

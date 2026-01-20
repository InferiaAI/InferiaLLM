from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
import os
from repositories.inventory_repo import InventoryRepository
from uuid import UUID

FILTRATION_DATABASE_URL = os.getenv("FILTRATION_DATABASE_URL")

router = APIRouter(prefix="/inventory", tags=["Inventory"])

class HeartbeatPayload(BaseModel):
    provider: str
    provider_instance_id: str
    gpu_allocated: int = 0
    vcpu_allocated: int = 0
    ram_gb_allocated: int = 0
    health_score: int = 100
    state: str = "READY"
    expose_url: str | None = None


from repositories.model_deployment_repo import ModelDeploymentRepository
from infra.redis_event_bus import RedisEventBus

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
                    deployment_id=d_id, 
                    endpoint=node["expose_url"]
                )

    # Sync Node State -> Deployment State
    # If the node is terminated or unhealthy, update the associated deployments
    if payload.state.lower() in ["terminated", "unhealthy", "failed"]:
        deployments = await inventory_repo.get_deployments_for_node(node["id"])
        target_state = "TERMINATED" if payload.state.lower() == "terminated" else "FAILED"
        
        for d_id in deployments:
            # Check current state to avoid overwriting user intent or loops
            current_d = await deployment_repo.get(d_id)
            if current_d and current_d["state"] not in ["TERMINATED", "FAILED", "STOPPED"]:
                # Nosana Edge Case: If job ends within 10 mins, mark unrelated failure as FAILED
                # We simply check if it's terminated quickly.
                # Assuming `payload.provider` is reliable or we check `current_d['provider']` if that existed (it doesn't directly, but via pool)
                # But the request came from payload.provider so we can trust it.
                
                final_state = target_state
                
                if payload.provider == "nosana" and target_state == "TERMINATED":
                    from datetime import datetime, timedelta, timezone
                    created_at = current_d.get("created_at")
                    if created_at:
                        if created_at.tzinfo is None:
                            created_at = created_at.replace(tzinfo=timezone.utc)
                        
                        now = datetime.now(timezone.utc)
                        duration = now - created_at
                        if duration < timedelta(minutes=10):
                            final_state = "FAILED"
                
                # Enforce Sticky Deployment: TERMINATED -> STOPPED
                if final_state == "TERMINATED":
                    final_state = "STOPPED"

                await deployment_repo.update_state(d_id, final_state)

    return {"status": "ok"}



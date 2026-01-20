from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from typing import List

from db.database import get_db
from db.models import Deployment as DBDeployment, InferenceLog as DBInferenceLog
from schemas.management import DeploymentCreate, DeploymentResponse
from schemas.logging import InferenceLogResponse
from schemas.auth import PermissionEnum
from management.dependencies import get_current_user_context
from rbac.authorization import authz_service
from gateway.mock_orchestration import mock_orchestration
from schemas.inference import ModelsListResponse

router = APIRouter(tags=["Deployments"])

@router.post("/deployments", response_model=DeploymentResponse, status_code=201)
async def create_deployment(
    deployment_data: DeploymentCreate,
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    user_ctx = get_current_user_context(request)
    authz_service.require_permission(user_ctx, PermissionEnum.DEPLOYMENT_CREATE)
    
    if not user_ctx.org_id:
        raise HTTPException(status_code=400, detail="Action requires organization context")

    new_deployment = DBDeployment(
        name=deployment_data.name,
        model_name=deployment_data.model_name,
        provider=deployment_data.provider,
        endpoint_url=deployment_data.endpoint_url,
        credentials_json=deployment_data.credentials_json,
        org_id=user_ctx.org_id
    )
    
    db.add(new_deployment)
    await db.commit()
    await db.refresh(new_deployment)

    # Log deployment creation
    from audit.service import audit_service
    from audit.api_models import AuditLogCreate
    
    await audit_service.log_event(
        db,
        AuditLogCreate(
            user_id=user_ctx.user_id,
            action="deployment.create",
            resource_type="deployment",
            resource_id=new_deployment.id,
            details={
                "name": new_deployment.name,
                "model": new_deployment.model_name,
                "provider": new_deployment.provider
            },
            status="success"
        )
    )

    return new_deployment

@router.get("/deployments", response_model=List[DeploymentResponse])
async def list_deployments(
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    user_ctx = get_current_user_context(request)
    authz_service.require_permission(user_ctx, PermissionEnum.DEPLOYMENT_LIST)
    
    if not user_ctx.org_id:
        return []

    deployments_result = await db.execute(select(DBDeployment).where(DBDeployment.org_id == user_ctx.org_id))
    return deployments_result.scalars().all()

@router.get("/deployments/{deployment_id}/logs", response_model=List[InferenceLogResponse])
async def get_deployment_logs(
    deployment_id: str,
    request: Request,
    limit: int = 50,
    offset: int = 0,
    db: AsyncSession = Depends(get_db)
):
    user_ctx = get_current_user_context(request)
    authz_service.require_permission(user_ctx, PermissionEnum.DEPLOYMENT_LIST)
    
    if not user_ctx.org_id:
         raise HTTPException(status_code=400, detail="Action requires organization context")
    
    deployment_result = await db.execute(
        select(DBDeployment).where(
            (DBDeployment.id == deployment_id) & 
            (DBDeployment.org_id == user_ctx.org_id)
        )
    )
    deployment = deployment_result.scalars().first()
    
    if not deployment:
        raise HTTPException(status_code=404, detail="Deployment not found")
    
    logs_result = await db.execute(
        select(DBInferenceLog)
        .where(DBInferenceLog.deployment_id == deployment_id)
        .order_by(DBInferenceLog.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    
    return logs_result.scalars().all()

@router.get("/deployments/recent-logs", response_model=List[InferenceLogResponse])
async def get_all_inference_logs(
    request: Request,
    limit: int = 20,
    offset: int = 0,
    db: AsyncSession = Depends(get_db)
):
    user_ctx = get_current_user_context(request)
    authz_service.require_permission(user_ctx, PermissionEnum.DEPLOYMENT_LIST)
    
    if not user_ctx.org_id:
         raise HTTPException(status_code=400, detail="Action requires organization context")
    
    # Join with deployments to filter by org_id
    logs_result = await db.execute(
        select(DBInferenceLog)
        .join(DBDeployment, DBInferenceLog.deployment_id == DBDeployment.id)
        .where(DBDeployment.org_id == user_ctx.org_id)
        .order_by(DBInferenceLog.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    
    return logs_result.scalars().all()

@router.delete("/deployments/{deployment_id}", status_code=204)
async def delete_deployment(
    deployment_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    user_ctx = get_current_user_context(request)
    # Using CREATE permission as proxy for management if DELETE doesn't exist, 
    # or specific DELETE if available. Checked schemas/auth.py in same turn.
    # Assuming DEPLOYMENT_CREATE implies management power for now, will adjust if DEPLOYMENT_DELETE exists.
    authz_service.require_permission(user_ctx, PermissionEnum.DEPLOYMENT_DELETE)
    
    if not user_ctx.org_id:
         raise HTTPException(status_code=400, detail="Action requires organization context")
    
    result = await db.execute(
        select(DBDeployment).where(
            (DBDeployment.id == deployment_id) & 
            (DBDeployment.org_id == user_ctx.org_id)
        )
    )
    deployment = result.scalars().first()
    
    if not deployment:
        raise HTTPException(status_code=404, detail="Deployment not found")
        
    await db.delete(deployment)
    await db.commit()
    
    # Log deletion
    from audit.service import audit_service
    from audit.api_models import AuditLogCreate
    
    await audit_service.log_event(
        db,
        AuditLogCreate(
            user_id=user_ctx.user_id,
            action="deployment.delete",
            resource_type="deployment",
            resource_id=deployment_id,
            details={
                "name": deployment.name,
                "model": deployment.model_name
            },
            status="success"
        )
    )
    return None

@router.get("/models", response_model=ModelsListResponse)
async def list_models(
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    user_ctx = get_current_user_context(request)
    authz_service.require_permission(user_ctx, PermissionEnum.MODEL_ACCESS)
    return mock_orchestration.get_available_models()

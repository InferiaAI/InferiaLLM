from fastapi import APIRouter, Depends, HTTPException, Request, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import delete, update
from sqlalchemy.future import select
from typing import List

from inferia.services.api_gateway.db.database import get_db
from inferia.services.api_gateway.db.models import (
    ApiKey as DBApiKey,
    Deployment as DBDeployment,
    InferenceLog as DBInferenceLog,
    Policy as DBPolicy,
)
from inferia.services.api_gateway.schemas.management import (
    DeploymentCreate,
    DeploymentResponse,
)
from inferia.services.api_gateway.schemas.logging import InferenceLogResponse
from inferia.services.api_gateway.schemas.auth import PermissionEnum
from inferia.services.api_gateway.management.dependencies import get_current_user_context
from inferia.services.api_gateway.rbac.authorization import authz_service
from inferia.services.api_gateway.schemas.inference import ModelInfo, ModelsListResponse

router = APIRouter(tags=["Deployments"])


@router.post("/deployments", response_model=DeploymentResponse, status_code=201)
async def create_deployment(
    deployment_data: DeploymentCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user_ctx = get_current_user_context(request)
    authz_service.require_permission(user_ctx, PermissionEnum.DEPLOYMENT_CREATE)

    if not user_ctx.org_id:
        raise HTTPException(
            status_code=400, detail="Action requires organization context"
        )

    new_deployment = DBDeployment(
        name=deployment_data.name,
        model_name=deployment_data.model_name,
        provider=deployment_data.provider,
        endpoint_url=deployment_data.endpoint_url,
        credentials_json=deployment_data.credentials_json,
        org_id=user_ctx.org_id,
    )

    db.add(new_deployment)
    await db.flush() # Get the ID before commit

    # Initialize default DISABLED policies for guardrail, rag, and prompt template
    # as requested: "they should be marked and flase not enabled"
    default_policies = [
        DBPolicy(
            policy_type="guardrail",
            config_json={
                "enabled": False,
                "guardrail_engine": "llm-guard",
                "input_scanners": [],
                "output_scanners": [],
                "toxicity_threshold": 0.5
            },
            org_id=user_ctx.org_id,
            deployment_id=new_deployment.id
        ),
        DBPolicy(
            policy_type="rag",
            config_json={
                "enabled": False,
                "default_collection": "default",
                "top_k": 3
            },
            org_id=user_ctx.org_id,
            deployment_id=new_deployment.id
        ),
        DBPolicy(
            policy_type="prompt_template",
            config_json={
                "enabled": False,
                "template_id": None,
                "template_vars": {}
            },
            org_id=user_ctx.org_id,
            deployment_id=new_deployment.id
        )
    ]
    
    for p in default_policies:
        db.add(p)

    await db.commit()
    await db.refresh(new_deployment)

    # Log deployment creation
    from inferia.services.api_gateway.audit.service import audit_service
    from inferia.services.api_gateway.models import AuditLogCreate

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
                "provider": new_deployment.provider,
            },
            status="success",
        ),
    )

    return new_deployment


@router.get("/deployments", response_model=List[DeploymentResponse])
async def list_deployments(
    request: Request,
    skip: int = Query(0, ge=0, description="Number of deployments to skip"),
    limit: int = Query(
        50, ge=1, le=100, description="Maximum number of deployments to return"
    ),
    db: AsyncSession = Depends(get_db),
):
    """List deployments with pagination."""
    user_ctx = get_current_user_context(request)
    authz_service.require_permission(user_ctx, PermissionEnum.DEPLOYMENT_LIST)

    if not user_ctx.org_id:
        return []

    deployments_result = await db.execute(
        select(DBDeployment)
        .where(DBDeployment.org_id == user_ctx.org_id)
        .offset(skip)
        .limit(limit)
    )
    return deployments_result.scalars().all()


@router.get(
    "/deployments/{deployment_id}/logs", response_model=List[InferenceLogResponse]
)
async def get_deployment_logs(
    deployment_id: str,
    request: Request,
    limit: int = Query(
        50, ge=1, le=100, description="Maximum number of logs to return"
    ),
    offset: int = Query(0, ge=0, description="Number of logs to skip"),
    db: AsyncSession = Depends(get_db),
):
    user_ctx = get_current_user_context(request)
    authz_service.require_permission(user_ctx, PermissionEnum.DEPLOYMENT_LIST)

    if not user_ctx.org_id:
        raise HTTPException(
            status_code=400, detail="Action requires organization context"
        )

    deployment_result = await db.execute(
        select(DBDeployment).where(
            (DBDeployment.id == deployment_id)
            & (DBDeployment.org_id == user_ctx.org_id)
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
    limit: int = Query(
        20, ge=1, le=100, description="Maximum number of logs to return"
    ),
    offset: int = Query(0, ge=0, description="Number of logs to skip"),
    db: AsyncSession = Depends(get_db),
):
    user_ctx = get_current_user_context(request)
    authz_service.require_permission(user_ctx, PermissionEnum.DEPLOYMENT_LIST)

    if not user_ctx.org_id:
        raise HTTPException(
            status_code=400, detail="Action requires organization context"
        )

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
    deployment_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    user_ctx = get_current_user_context(request)
    # Using CREATE permission as proxy for management if DELETE doesn't exist,
    # or specific DELETE if available. Checked schemas/auth.py in same turn.
    # Assuming DEPLOYMENT_CREATE implies management power for now, will adjust if DEPLOYMENT_DELETE exists.
    authz_service.require_permission(user_ctx, PermissionEnum.DEPLOYMENT_DELETE)

    if not user_ctx.org_id:
        raise HTTPException(
            status_code=400, detail="Action requires organization context"
        )

    result = await db.execute(
        select(DBDeployment).where(
            (DBDeployment.id == deployment_id)
            & (DBDeployment.org_id == user_ctx.org_id)
        )
    )
    deployment = result.scalars().first()

    if not deployment:
        raise HTTPException(status_code=404, detail="Deployment not found")

    # Defensively clean dependent rows for databases where FK actions were not
    # created with ON DELETE behavior.
    await db.execute(
        update(DBPolicy)
        .where(DBPolicy.deployment_id == deployment.id)
        .values(deployment_id=None)
    )
    await db.execute(
        update(DBApiKey)
        .where(DBApiKey.deployment_id == deployment.id)
        .values(deployment_id=None)
    )
    await db.execute(
        delete(DBInferenceLog).where(DBInferenceLog.deployment_id == deployment.id)
    )
    await db.delete(deployment)
    await db.commit()

    # Log deletion
    from inferia.services.api_gateway.audit.service import audit_service
    from inferia.services.api_gateway.models import AuditLogCreate

    await audit_service.log_event(
        db,
        AuditLogCreate(
            user_id=user_ctx.user_id,
            action="deployment.delete",
            resource_type="deployment",
            resource_id=deployment_id,
            details={"name": deployment.name, "model": deployment.model_name},
            status="success",
        ),
    )
    return None


@router.get("/models", response_model=ModelsListResponse)
async def list_models(request: Request, db: AsyncSession = Depends(get_db)):
    user_ctx = get_current_user_context(request)
    authz_service.require_permission(user_ctx, PermissionEnum.MODEL_ACCESS)

    # Get available models from database (real deployments for this org)
    if not user_ctx.org_id:
        return ModelsListResponse(data=[])

    result = await db.execute(
        select(DBDeployment).where(
            (DBDeployment.org_id == user_ctx.org_id)
            & (DBDeployment.state.in_(["RUNNING", "READY", "ready"]))
        )
    )
    deployments = result.scalars().all()

    models = [
        ModelInfo(
            id=d.model_name,
            created=int(d.created_at.timestamp()) if d.created_at else 0,
            owned_by=d.org_id,
            description=f"Active deployment: {d.name}",
        )
        for d in deployments
    ]

    return ModelsListResponse(data=models)

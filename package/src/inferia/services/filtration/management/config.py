from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from typing import List, Optional
from datetime import datetime

from db.database import get_db
from db.models import Policy as DBPolicy, Usage as DBUsage, ApiKey as DBApiKey
from schemas.config import ConfigUpdateRequest, ConfigResponse, UsageStatsResponse
from management.dependencies import get_current_user_context

router = APIRouter(tags=["Configuration"])

@router.post("/config", status_code=200)
async def update_config(
    config_data: ConfigUpdateRequest,
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    user_ctx = get_current_user_context(request)
    if not user_ctx.org_id:
          raise HTTPException(status_code=400, detail="Action requires organization context")

    if "admin" not in user_ctx.roles:
        raise HTTPException(status_code=403, detail="Only admins can update config")
        
    stmt = select(DBPolicy).where(
        (DBPolicy.org_id == user_ctx.org_id) & 
        (DBPolicy.policy_type == config_data.policy_type)
    )
    
    if config_data.deployment_id:
        stmt = stmt.where(DBPolicy.deployment_id == config_data.deployment_id)
    else:
        stmt = stmt.where(DBPolicy.deployment_id.is_(None))
        
    policy_result = await db.execute(stmt)
    policy = policy_result.scalars().first()
    
    if policy:
        policy.config_json = dict(config_data.config_json)
        from sqlalchemy.orm.attributes import flag_modified
        flag_modified(policy, "config_json")
    else:
        policy = DBPolicy(
            org_id=user_ctx.org_id,
            policy_type=config_data.policy_type,
            deployment_id=config_data.deployment_id,
            config_json=config_data.config_json
        )
        db.add(policy)
        
    await db.commit()
    await db.refresh(policy)

    # Log to audit service
    from audit.service import audit_service
    from audit.api_models import AuditLogCreate

    await audit_service.log_event(
        db,
        AuditLogCreate(
            user_id=user_ctx.user_id,
            action="config.update",
            resource_type="policy",
            resource_id=str(policy.id),
            details={
                "policy_type": config_data.policy_type,
                "deployment_id": config_data.deployment_id
            },
            status="success"
        )
    )

    return {"status": "success", "policy_type": config_data.policy_type}

@router.get("/config/{policy_type}", response_model=ConfigResponse)
async def get_config(
    policy_type: str,
    deployment_id: Optional[str] = None,
    request: Request = None,
    db: AsyncSession = Depends(get_db)
):
    user_ctx = get_current_user_context(request)
    if not user_ctx.org_id:
         return ConfigResponse(
            policy_type=policy_type,
            config_json={},
            updated_at=datetime.utcnow()
        )
    
    stmt = select(DBPolicy).where(
        (DBPolicy.org_id == user_ctx.org_id) & 
        (DBPolicy.policy_type == policy_type)
    )
    
    if deployment_id:
        stmt = stmt.where(DBPolicy.deployment_id == deployment_id)
    else:
        stmt = stmt.where(DBPolicy.deployment_id.is_(None))
        
    result = await db.execute(stmt)
    policy = result.scalars().first()
    
    if not policy:
        return ConfigResponse(
            policy_type=policy_type,
            config_json={},
            updated_at=datetime.utcnow()
        )
        
    return policy

@router.get("/config/quota/usage", response_model=List[UsageStatsResponse])
async def get_usage_stats(
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    user_ctx = get_current_user_context(request)
    if not user_ctx.org_id:
         return []
    
    keys_result = await db.execute(select(DBApiKey).where(DBApiKey.org_id == user_ctx.org_id))
    keys = keys_result.scalars().all()
    
    stats = []
    today = datetime.utcnow().date()
    
    for key in keys:
        usage_result = await db.execute(
            select(DBUsage).where(
                (DBUsage.user_id == f"apikey:{key.id}") &
                (DBUsage.date == today)
            )
        )
        usage_records = usage_result.scalars().all()
        
        total_requests = sum(r.request_count for r in usage_records)
        total_tokens = sum(r.total_tokens for r in usage_records)
        
        stats.append(UsageStatsResponse(
            key_name=key.name,
            key_prefix=key.prefix,
            requests=total_requests,
            tokens=total_tokens
        ))
        
    return stats

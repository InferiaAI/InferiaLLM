from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from typing import List
import uuid
from datetime import datetime, timezone

def utcnow_naive():
    return datetime.now(timezone.utc).replace(tzinfo=None)

from db.database import get_db
from db.models import ApiKey as DBApiKey
from schemas.management import ApiKeyCreate, ApiKeyResponse, ApiKeyCreatedResponse
from schemas.auth import PermissionEnum
from management.dependencies import get_current_user_context
from rbac.authorization import authz_service
from rbac.auth import auth_service

router = APIRouter(tags=["API Keys"])


@router.post("/api-keys", response_model=ApiKeyCreatedResponse, status_code=201)
async def create_api_key(
    key_data: ApiKeyCreate,
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    user_ctx = get_current_user_context(request)
    authz_service.require_permission(user_ctx, PermissionEnum.API_KEY_CREATE)
    
    if not user_ctx.org_id:
        raise HTTPException(status_code=400, detail="Action requires organization context")
    
    raw_key = f"sk-{uuid.uuid4().hex}"
    key_hash = auth_service.get_password_hash(raw_key)
    prefix = raw_key[:6] + "..." 
    
    new_key = DBApiKey(
        name=key_data.name,
        key_hash=key_hash,
        prefix=prefix,
        org_id=user_ctx.org_id,
        deployment_id=key_data.deployment_id
    )
    
    db.add(new_key)
    await db.commit()
    await db.refresh(new_key)
    
    # Log API Key creation
    from audit.service import audit_service
    from audit.api_models import AuditLogCreate
    
    await audit_service.log_event(
        db,
        AuditLogCreate(
            user_id=user_ctx.user_id,
            action="api_key.create",
            resource_type="api_key",
            resource_id=new_key.id,
            details={
                "name": new_key.name,
                "prefix": new_key.prefix,
                "deployment_id": new_key.deployment_id
            },
            status="success"
        )
    )

    return ApiKeyCreatedResponse(
        id=new_key.id,
        name=new_key.name,
        prefix=new_key.prefix,
        is_active=new_key.is_active,
        created_at=new_key.created_at,
        last_used_at=new_key.last_used_at,
        secret_key=raw_key
    )

@router.get("/api-keys", response_model=List[ApiKeyResponse])
async def list_api_keys(
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    user_ctx = get_current_user_context(request)
    authz_service.require_permission(user_ctx, PermissionEnum.API_KEY_LIST)
    
    if not user_ctx.org_id:
        return []
    
    keys_result = await db.execute(select(DBApiKey).where(DBApiKey.org_id == user_ctx.org_id))
    return keys_result.scalars().all()

@router.delete("/api-keys/{key_id}", status_code=204)
async def revoke_api_key(
    key_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    user_ctx = get_current_user_context(request)
    authz_service.require_permission(user_ctx, PermissionEnum.API_KEY_REVOKE)
    
    if not user_ctx.org_id:
        raise HTTPException(status_code=400, detail="Action requires organization context")
        
    result = await db.execute(
        select(DBApiKey).where(
            (DBApiKey.id == key_id) & 
            (DBApiKey.org_id == user_ctx.org_id)
        )
    )
    api_key = result.scalars().first()
    
    if not api_key:
        raise HTTPException(status_code=404, detail="API Key not found")
        
    # Soft delete / deactivation
    api_key.is_active = False
    api_key.last_used_at = utcnow_naive()
 # Mark revocation time roughly
    
    await db.commit()
    
    # Log revocation
    from audit.service import audit_service
    from audit.api_models import AuditLogCreate
    
    await audit_service.log_event(
        db,
        AuditLogCreate(
            user_id=user_ctx.user_id,
            action="api_key.revoke",
            resource_type="api_key",
            resource_id=key_id,
            details={
                "name": api_key.name,
                "prefix": api_key.prefix
            },
            status="success"
        )
    )
    return None

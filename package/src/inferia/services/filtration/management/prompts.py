from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from typing import List

from db.database import get_db
from db.models import Policy as DBPolicy
from schemas.prompt import PromptTemplateCreate, PromptTemplateResponse
from schemas.auth import PermissionEnum
from management.dependencies import get_current_user_context
from rbac.authorization import authz_service

router = APIRouter(tags=["Prompt Templates"])

@router.post("/templates", response_model=PromptTemplateResponse, status_code=201)
async def create_template(
    template_data: PromptTemplateCreate,
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    user_ctx = get_current_user_context(request)
    # Using specific permission for prompt templates now
    authz_service.require_permission(user_ctx, PermissionEnum.PROMPT_CREATE)
         
    if not user_ctx.org_id:
        raise HTTPException(status_code=400, detail="Action requires organization context")
    
    config_json = {
        "id": template_data.template_id,
        "content": template_data.content,
        "description": template_data.description
    }

    new_policy = DBPolicy(
        org_id=user_ctx.org_id,
        policy_type="prompt_template",
        deployment_id=template_data.deployment_id,
        config_json=config_json
    )
    
    db.add(new_policy)
    await db.commit()
    await db.refresh(new_policy)

    # Log to audit service
    from audit.service import audit_service
    from audit.api_models import AuditLogCreate

    await audit_service.log_event(
        db,
        AuditLogCreate(
            user_id=user_ctx.user_id,
            action="prompt_template.create",
            resource_type="prompt_template",
            resource_id=template_data.template_id,
            details={
                "description": template_data.description
            },
            status="success"
        )
    )
    
    return PromptTemplateResponse(
        template_id=template_data.template_id,
        content=template_data.content,
        description=template_data.description,
        updated_at=new_policy.updated_at
    )

@router.get("/templates", response_model=List[PromptTemplateResponse])
async def list_templates(
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    user_ctx = get_current_user_context(request)
    # Require generic list permission
    authz_service.require_permission(user_ctx, PermissionEnum.PROMPT_LIST)
    
    if not user_ctx.org_id:
        return []
        
    stmt = select(DBPolicy).where(
        (DBPolicy.org_id == user_ctx.org_id) &
        (DBPolicy.policy_type == "prompt_template")
    )
    
    result = await db.execute(stmt)
    policies = result.scalars().all()
    
    templates = []
    for p in policies:
        if p.config_json:
            templates.append(PromptTemplateResponse(
                template_id=p.config_json.get("id", "unknown"),
                content=p.config_json.get("content", ""),
                description=p.config_json.get("description"),
                updated_at=p.updated_at
            ))
            
    return templates

@router.delete("/templates/{template_id}", status_code=204)
async def delete_template(
    template_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    user_ctx = get_current_user_context(request)
    authz_service.require_permission(user_ctx, PermissionEnum.PROMPT_DELETE)
    
    if not user_ctx.org_id:
        raise HTTPException(status_code=400, detail="Action requires organization context")
        
    # Query for the policy (prompt template)
    stmt = select(DBPolicy).where(
        (DBPolicy.org_id == user_ctx.org_id) &
        (DBPolicy.policy_type == "prompt_template")
    )
    result = await db.execute(stmt)
    policies = result.scalars().all()
    
    # Filter in python as config_json is JSONB
    policy_to_delete = None
    for p in policies:
        if p.config_json and p.config_json.get("id") == template_id:
            policy_to_delete = p
            break
            
    if not policy_to_delete:
        raise HTTPException(status_code=404, detail="Template not found")
        
    await db.delete(policy_to_delete)
    await db.commit()
    
    # Log deletion
    from audit.service import audit_service
    from audit.api_models import AuditLogCreate
    
    await audit_service.log_event(
        db,
        AuditLogCreate(
            user_id=user_ctx.user_id,
            action="prompt_template.delete",
            resource_type="prompt_template",
            resource_id=template_id,
            details={
                "description": policy_to_delete.config_json.get("description")
            },
            status="success"
        )
    )
    return None

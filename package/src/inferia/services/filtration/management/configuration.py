from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from typing import List, Optional, Dict, Any
from datetime import datetime, timezone

def utcnow_naive():
    return datetime.now(timezone.utc).replace(tzinfo=None)

from db.database import get_db
from db.models import Policy as DBPolicy, Usage as DBUsage, ApiKey as DBApiKey
from schemas.config import ConfigUpdateRequest, ConfigResponse, UsageStatsResponse
from management.dependencies import get_current_user_context
# New imports for local provider config
from pydantic import BaseModel, Field
from pathlib import Path
import json
import os
from config import settings
from config import ProvidersConfig, AWSConfig, ChromaConfig, GroqConfig, LakeraConfig, NosanaConfig, AkashConfig, CloudConfig, VectorDBConfig, GuardrailsConfig, DePINConfig

router = APIRouter(tags=["Configuration"])

# --- Local Provider Configuration ---
# Re-use models from config.py or redefine to ensure API schema matches.
# To allow partial updates in API, dependencies often need optional fields.
# The core config models already have optional fields, so we can inherit or wrap.

# We will use the models defined in config.py but ensuring we can mask them.
# Pydantic models are defined in config.py, we can import them.

class ProviderConfigResponse(BaseModel):
    providers: ProvidersConfig

def _mask_secret(value: Optional[str]) -> Optional[str]:
    if not value or len(value) < 8:
        return value # Too short to mask meaningfully or empty
    return f"{value[:4]}...{value[-4:]}"

def _mask_config(config: ProvidersConfig) -> ProvidersConfig:
    # Create a copy to mask
    masked = config.model_copy(deep=True)
    
    # Cloud
    if masked.cloud.aws.secret_access_key:
        masked.cloud.aws.secret_access_key = "********"
    if masked.cloud.aws.access_key_id:
        masked.cloud.aws.access_key_id = _mask_secret(masked.cloud.aws.access_key_id)
        
    # VectorDB
    if masked.vectordb.chroma.api_key:
        masked.vectordb.chroma.api_key = _mask_secret(masked.vectordb.chroma.api_key)
        
    # Guardrails
    if masked.guardrails.groq.api_key:
        masked.guardrails.groq.api_key = _mask_secret(masked.guardrails.groq.api_key)
    if masked.guardrails.lakera.api_key:
         masked.guardrails.lakera.api_key = _mask_secret(masked.guardrails.lakera.api_key)
         
    # DePIN
    if masked.depin.nosana.wallet_private_key:
        masked.depin.nosana.wallet_private_key = "********"
    if masked.depin.akash.mnemonic:
        masked.depin.akash.mnemonic = "********"
        
    return masked

@router.get("/config/providers", response_model=ProviderConfigResponse)
async def get_provider_config(
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    """
    Get current provider configuration. Returns masked secrets.
    Requires Admin role.
    """
    user_ctx = get_current_user_context(request)
    if not "admin" in user_ctx.roles:
         raise HTTPException(status_code=403, detail="Only admins can view provider config")

    masked_providers = _mask_config(settings.providers)
    return ProviderConfigResponse(providers=masked_providers)

@router.post("/config/providers")
async def update_provider_config(
    wrapper: ProviderConfigResponse, # Expects { "providers": { ... } }
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    """
    Update provider configuration. Writes to ~/.inferia/config.json.
    Requires Admin role.
    """
    user_ctx = get_current_user_context(request)
    if not "admin" in user_ctx.roles:
         raise HTTPException(status_code=403, detail="Only admins can update provider config")

    # 1. Update DB and Local Cache
    from management.config_manager import config_manager
    
    new_data = wrapper.providers.model_dump(exclude_unset=True)
    
    # Structure for DB storage
    db_config = {"providers": new_data}
    
    try:
        await config_manager.save_config(db, db_config)
        
        # Log update status for guardrails
        if "guardrails" in new_data:
            groq_new = new_data["guardrails"].get("groq", {}).get("api_key")
            if groq_new:
                print(f"[DEBUG] Config Update: New Groq API Key received: {groq_new[:6]}...")
    except Exception as e:
        print(f"[DEBUG] Failed to write config: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to save config: {e}")
        
    # Refresh data engine client if vectordb config was updated
    if wrapper.providers.vectordb:
        try:
             # Run potentially blocking initialization in a separate thread
             import asyncio
             from data.engine import data_engine
             from fastapi import BackgroundTasks
             
             # If we have BackgroundTasks in context, use them (not available here in endpoint signature)
             # So we use asyncio.create_task with to_thread to make it non-blocking
             # But we need to ensure it doesn't fail silently.
             
             async def refresh_chroma():
                 try:
                     await asyncio.to_thread(data_engine.initialize_client)
                 except Exception as e:
                     print(f"[DEBUG] Background Chroma refresh failed: {e}")

             asyncio.create_task(refresh_chroma())
             
        except Exception as e:
             print(f"[DEBUG] Failed to trigger data engine refresh: {e}")
             
    if wrapper.providers.guardrails:
        try:
            # Also wrap guardrail refresh
            from guardrail.config import guardrail_settings
            async def refresh_guardrails():
                try:
                    await asyncio.to_thread(guardrail_settings.refresh_from_main_settings)
                except Exception as e:
                    print(f"[DEBUG] Background Guardrail refresh failed: {e}")
                    
            asyncio.create_task(refresh_guardrails())
        except Exception as e:
            print(f"[DEBUG] Failed to trigger guardrail refresh: {e}")

    return {"status": "ok", "message": "Configuration saved to database"}

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
    today = datetime.now(timezone.utc).date()
    
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
            updated_at=utcnow_naive()
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
            updated_at=utcnow_naive()
        )
        
    return policy

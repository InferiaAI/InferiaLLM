from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from typing import List, Optional, Dict, Any
from datetime import datetime, timezone
import asyncio
import logging

logger = logging.getLogger(__name__)


def utcnow_naive():
    return datetime.now(timezone.utc).replace(tzinfo=None)


from inferia.services.api_gateway.db.database import get_db
from inferia.services.api_gateway.gateway.http_client import gateway_http_client
from inferia.services.api_gateway.db.models import (
    Policy as DBPolicy,
    Usage as DBUsage,
    ApiKey as DBApiKey,
)
from inferia.services.api_gateway.schemas.config import (
    ConfigUpdateRequest,
    ConfigResponse,
    UsageStatsResponse,
)
from inferia.services.api_gateway.management.dependencies import (
    get_current_user_context,
)

# New imports for local provider config
from pydantic import BaseModel, Field
from pathlib import Path
import json
import os
from inferia.services.api_gateway.config import settings
from inferia.services.api_gateway.config import (
    ProvidersConfig,
    AWSConfig,
    ChromaConfig,
    GroqConfig,
    LakeraConfig,
    NosanaConfig,
    ProviderCredential,
    AkashConfig,
    CloudConfig,
    VectorDBConfig,
    GuardrailsConfig,
    DePINConfig,
)

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
        return value  # Too short to mask meaningfully or empty
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
        masked.guardrails.lakera.api_key = _mask_secret(
            masked.guardrails.lakera.api_key
        )

    # DePIN
    if masked.depin.nosana.wallet_private_key:
        masked.depin.nosana.wallet_private_key = "********"
    if masked.depin.nosana.api_key:
        masked.depin.nosana.api_key = _mask_secret(masked.depin.nosana.api_key)
    # Mask named credentials in api_keys list
    for i, entry in enumerate(masked.depin.nosana.api_keys):
        if isinstance(entry, dict):
            if entry.get("key"):
                entry["key"] = _mask_secret(entry["key"])
        else:
            if entry.key:
                entry.key = _mask_secret(entry.key)
    if masked.depin.akash.mnemonic:
        masked.depin.akash.mnemonic = "********"

    return masked


@router.get("/config/providers", response_model=ProviderConfigResponse)
async def get_provider_config(request: Request, db: AsyncSession = Depends(get_db)):
    """
    Get current provider configuration. Returns masked secrets.
    Requires Admin role.
    """
    user_ctx = get_current_user_context(request)
    if not "admin" in user_ctx.roles:
        raise HTTPException(
            status_code=403, detail="Only admins can view provider config"
        )

    masked_providers = _mask_config(settings.providers)
    return ProviderConfigResponse(providers=masked_providers)


@router.post("/config/providers")
async def update_provider_config(
    wrapper: ProviderConfigResponse,  # Expects { "providers": { ... } }
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Update provider configuration. Persists to the system database.
    Requires Admin role.
    """
    user_ctx = get_current_user_context(request)
    if not "admin" in user_ctx.roles:
        raise HTTPException(
            status_code=403, detail="Only admins can update provider config"
        )

    # 1. Update DB and Local Cache
    from inferia.services.api_gateway.management.config_manager import config_manager

    new_data = wrapper.providers.model_dump(exclude_unset=True)

    # Structure for DB storage
    db_config = {"providers": new_data}

    try:
        await config_manager.save_config(db, db_config)

        # Log update status for guardrails
        if "guardrails" in new_data:
            groq_new = new_data["guardrails"].get("groq", {}).get("api_key")
            if groq_new:
                logger.info(
                    "Config Update: New Groq API Key received: %s...", groq_new[:6]
                )
    except Exception as e:
        logger.error("Failed to write config: %s", e)
        raise HTTPException(status_code=500, detail=f"Failed to save config: {e}")

    # Data engine and guardrail refreshes are now handled by their respective microservices

    return {"status": "ok", "message": "Configuration saved to database"}


@router.post("/config", status_code=200)
async def update_config(
    config_data: ConfigUpdateRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user_ctx = get_current_user_context(request)
    if not user_ctx.org_id:
        raise HTTPException(
            status_code=400, detail="Action requires organization context"
        )

    if "admin" not in user_ctx.roles:
        raise HTTPException(status_code=403, detail="Only admins can update config")

    stmt = select(DBPolicy).where(
        (DBPolicy.org_id == user_ctx.org_id)
        & (DBPolicy.policy_type == config_data.policy_type)
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
            config_json=config_data.config_json,
        )
        db.add(policy)

    await db.commit()
    await db.refresh(policy)

    # Log to audit service
    from inferia.services.api_gateway.audit.service import audit_service
    from inferia.services.api_gateway.models import AuditLogCreate

    await audit_service.log_event(
        db,
        AuditLogCreate(
            user_id=user_ctx.user_id,
            action="config.update",
            resource_type="policy",
            resource_id=str(policy.id),
            details={
                "policy_type": config_data.policy_type,
                "deployment_id": config_data.deployment_id,
            },
            status="success",
        ),
    )

    return {"status": "success", "policy_type": config_data.policy_type}


@router.get("/config/quota/usage", response_model=List[UsageStatsResponse])
async def get_usage_stats(request: Request, db: AsyncSession = Depends(get_db)):
    user_ctx = get_current_user_context(request)
    if not user_ctx.org_id:
        return []

    keys_result = await db.execute(
        select(DBApiKey).where(DBApiKey.org_id == user_ctx.org_id)
    )
    keys = keys_result.scalars().all()

    stats = []
    today = datetime.now(timezone.utc).date()

    for key in keys:
        usage_result = await db.execute(
            select(DBUsage).where(
                (DBUsage.user_id == f"apikey:{key.id}") & (DBUsage.date == today)
            )
        )
        usage_records = usage_result.scalars().all()

        total_requests = sum(r.request_count for r in usage_records)
        total_tokens = sum(r.total_tokens for r in usage_records)

        stats.append(
            UsageStatsResponse(
                key_name=key.name,
                key_prefix=key.prefix,
                requests=total_requests,
                tokens=total_tokens,
            )
        )

    return stats


@router.get("/config/{policy_type}", response_model=ConfigResponse)
async def get_config(
    policy_type: str,
    request: Request,
    deployment_id: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    user_ctx = get_current_user_context(request)
    if not user_ctx.org_id:
        return ConfigResponse(
            policy_type=policy_type, config_json={}, updated_at=utcnow_naive()
        )

    stmt = select(DBPolicy).where(
        (DBPolicy.org_id == user_ctx.org_id) & (DBPolicy.policy_type == policy_type)
    )

    if deployment_id:
        stmt = stmt.where(DBPolicy.deployment_id == deployment_id)
    else:
        stmt = stmt.where(DBPolicy.deployment_id.is_(None))

    result = await db.execute(stmt)
    policy = result.scalars().first()

    if not policy:
        return ConfigResponse(
            policy_type=policy_type, config_json={}, updated_at=utcnow_naive()
        )

    return policy


# --- Universal Provider Credential Management ---
# Works for ANY provider (nosana, akash, aws, etc.)


class ProviderCredentialCreate(BaseModel):
    name: str
    credential_type: str  # e.g., 'api_key', 'wallet', 'mnemonic', 'access_key'
    value: str


class ProviderCredentialUpdate(BaseModel):
    name: str
    credential_type: Optional[str] = None
    value: Optional[str] = None
    is_active: Optional[bool] = None


class ProviderCredentialResponse(BaseModel):
    provider: str
    name: str
    credential_type: str
    is_active: bool
    created_at: Optional[datetime] = None


class ProviderCredentialListResponse(BaseModel):
    credentials: List[ProviderCredentialResponse]


def _mask_credential(value: str) -> str:
    """Mask credential for display: show first 4 and last 4 chars."""
    if not value or len(value) < 8:
        return "****"
    return f"{value[:4]}****{value[-4:]}"


# Legacy migration: Convert old nosana config to new credential system
def _get_nosana_credentials_from_config() -> List[ProviderCredential]:
    """Extract nosana credentials from legacy config for migration."""
    credentials = []
    nosana_config = settings.providers.depin.nosana

    # Migrate legacy api_key
    if nosana_config.api_key:
        credentials.append(
            ProviderCredential(
                provider="nosana",
                name="default",
                credential_type="api_key",
                value=nosana_config.api_key,
                is_active=True,
            )
        )

    # Migrate api_keys list from config (using raw dict access for backward compatibility)
    raw_config = settings.providers.model_dump()
    api_keys = raw_config.get("depin", {}).get("nosana", {}).get("api_keys", [])
    for api_key_entry in api_keys:
        # Check if this key is not the legacy one already added
        if api_key_entry.get("key") != nosana_config.api_key:
            credentials.append(
                ProviderCredential(
                    provider="nosana",
                    name=api_key_entry.get("name", "unnamed"),
                    credential_type="api_key",
                    value=api_key_entry.get("key", ""),
                    is_active=api_key_entry.get("is_active", True),
                )
            )

    return credentials


@router.get(
    "/config/providers/{provider}/credentials",
    response_model=ProviderCredentialListResponse,
)
async def list_provider_credentials(
    provider: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    List all credentials for a provider (with masked values).
    Requires Admin role.
    Works for ANY provider: nosana, akash, aws, etc.
    """
    user_ctx = get_current_user_context(request)
    if "admin" not in user_ctx.roles:
        raise HTTPException(
            status_code=403,
            detail=f"Only admins can view {provider} credentials",
        )

    # TODO: In production, fetch from provider_credentials table
    # For now, use config-based storage with migration
    credentials = []

    if provider == "nosana":
        credentials = _get_nosana_credentials_from_config()
    elif provider == "akash":
        # Check for legacy mnemonic
        mnemonic = settings.providers.depin.akash.mnemonic
        if mnemonic:
            credentials.append(
                ProviderCredential(
                    provider="akash",
                    name="default",
                    credential_type="mnemonic",
                    value=mnemonic,
                    is_active=True,
                )
            )

    return ProviderCredentialListResponse(
        credentials=[
            ProviderCredentialResponse(
                provider=c.provider,
                name=c.name,
                credential_type=c.credential_type,
                is_active=c.is_active,
                created_at=None,  # Will be added when persisted to DB
            )
            for c in credentials
        ]
    )


@router.post("/config/providers/{provider}/credentials")
async def add_provider_credential(
    provider: str,
    credential_data: ProviderCredentialCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Add a new credential for any provider.
    Requires Admin role.

    Examples:
    - provider="nosana", credential_type="api_key"
    - provider="akash", credential_type="mnemonic"
    - provider="aws", credential_type="access_key"
    """
    user_ctx = get_current_user_context(request)
    if "admin" not in user_ctx.roles:
        raise HTTPException(
            status_code=403,
            detail=f"Only admins can add {provider} credentials",
        )

    from inferia.services.api_gateway.management.config_manager import config_manager

    # Build new credential
    new_credential = ProviderCredential(
        provider=provider,
        name=credential_data.name,
        credential_type=credential_data.credential_type,
        value=credential_data.value,
        is_active=True,
    )

    # Get current config
    current_config = settings.providers.model_dump()

    # Store based on provider type
    if provider == "nosana":
        if "depin" not in current_config:
            current_config["depin"] = {}
        if "nosana" not in current_config["depin"]:
            current_config["depin"]["nosana"] = {}
        if "api_keys" not in current_config["depin"]["nosana"]:
            current_config["depin"]["nosana"]["api_keys"] = []

        # Check for duplicate names
        existing_names = {
            k["name"] for k in current_config["depin"]["nosana"]["api_keys"]
        }
        if credential_data.name in existing_names:
            raise HTTPException(
                status_code=400,
                detail=f"Credential with name '{credential_data.name}' already exists for {provider}",
            )

        current_config["depin"]["nosana"]["api_keys"].append(
            {
                "name": new_credential.name,
                "key": new_credential.value,
                "is_active": new_credential.is_active,
            }
        )
    elif provider == "akash":
        # For akash, we might store in a different structure
        # This shows how easy it is to extend to new providers
        if "depin" not in current_config:
            current_config["depin"] = {}
        if "akash" not in current_config["depin"]:
            current_config["depin"]["akash"] = {}
        if "wallets" not in current_config["depin"]["akash"]:
            current_config["depin"]["akash"]["wallets"] = []

        current_config["depin"]["akash"]["wallets"].append(
            {
                "name": new_credential.name,
                "mnemonic": new_credential.value,
                "is_active": new_credential.is_active,
            }
        )
    else:
        raise HTTPException(
            status_code=400,
            detail=f"Provider '{provider}' not yet supported for credential management",
        )

    db_config = {"providers": current_config}

    try:
        await config_manager.save_config(db, db_config)
        logger.info(f"Added new credential for {provider}: {credential_data.name}")
    except Exception as e:
        logger.error(f"Failed to add credential: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to save credential: {e}")

    return {
        "status": "ok",
        "message": f"Credential '{credential_data.name}' added successfully for {provider}",
        "provider": provider,
        "name": credential_data.name,
    }


@router.put("/config/providers/{provider}/credentials/{credential_name}")
async def update_provider_credential(
    provider: str,
    credential_name: str,
    credential_data: ProviderCredentialUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Update a credential for any provider.
    Requires Admin role.
    """
    user_ctx = get_current_user_context(request)
    if "admin" not in user_ctx.roles:
        raise HTTPException(
            status_code=403,
            detail=f"Only admins can update {provider} credentials",
        )

    from inferia.services.api_gateway.management.config_manager import config_manager

    current_config = settings.providers.model_dump()
    updated = False

    if provider == "nosana":
        api_keys = current_config.get("depin", {}).get("nosana", {}).get("api_keys", [])
        for key_config in api_keys:
            if key_config["name"] == credential_name:
                if credential_data.value is not None:
                    key_config["key"] = credential_data.value
                if credential_data.is_active is not None:
                    key_config["is_active"] = credential_data.is_active
                updated = True
                break
    elif provider == "akash":
        wallets = current_config.get("depin", {}).get("akash", {}).get("wallets", [])
        for wallet_config in wallets:
            if wallet_config["name"] == credential_name:
                if credential_data.value is not None:
                    wallet_config["mnemonic"] = credential_data.value
                if credential_data.is_active is not None:
                    wallet_config["is_active"] = credential_data.is_active
                updated = True
                break

    if not updated:
        raise HTTPException(
            status_code=404,
            detail=f"Credential '{credential_name}' not found for {provider}",
        )

    db_config = {"providers": current_config}

    try:
        await config_manager.save_config(db, db_config)
        logger.info(f"Updated credential for {provider}: {credential_name}")
    except Exception as e:
        logger.error(f"Failed to update credential: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to update credential: {e}")

    return {
        "status": "ok",
        "message": f"Credential '{credential_name}' updated successfully for {provider}",
    }


@router.delete("/config/providers/{provider}/credentials/{credential_name}")
async def delete_provider_credential(
    provider: str,
    credential_name: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Delete a credential for any provider.
    Requires Admin role.
    """
    user_ctx = get_current_user_context(request)
    if "admin" not in user_ctx.roles:
        raise HTTPException(
            status_code=403,
            detail=f"Only admins can delete {provider} credentials",
        )

    from inferia.services.api_gateway.management.config_manager import config_manager

    # 1. Cascade cleanup: Stop deployments and delete compute pools associated with this credential
    try:
        await _cleanup_provider_resources(
            provider, credential_name, user_ctx.user_id, user_ctx.org_id
        )
    except Exception as e:
        logger.warning(f"Resource cleanup during credential deletion failed: {e}")

    current_config = settings.providers.model_dump()
    deleted = False

    if provider == "nosana":
        api_keys = current_config.get("depin", {}).get("nosana", {}).get("api_keys", [])
        for i, key_config in enumerate(api_keys):
            if key_config["name"] == credential_name:
                api_keys.pop(i)
                deleted = True
                break
    elif provider == "akash":
        wallets = current_config.get("depin", {}).get("akash", {}).get("wallets", [])
        for i, wallet_config in enumerate(wallets):
            if wallet_config["name"] == credential_name:
                wallets.pop(i)
                deleted = True
                break

    if not deleted:
        raise HTTPException(
            status_code=404,
            detail=f"Credential '{credential_name}' not found for {provider}",
        )

    db_config = {"providers": current_config}

    try:
        await config_manager.save_config(db, db_config)
        logger.info(f"Deleted credential for {provider}: {credential_name}")
    except Exception as e:
        logger.error(f"Failed to delete credential: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to delete credential: {e}")

    return {
        "status": "ok",
        "message": f"Credential '{credential_name}' deleted successfully for {provider}",
    }


# Legacy endpoints for backward compatibility (redirect to new universal endpoints)
@router.get("/config/providers/nosana/keys")
async def list_nosana_api_keys_legacy(
    request: Request, db: AsyncSession = Depends(get_db)
):
    """Legacy endpoint - redirects to universal credential system."""
    return await list_provider_credentials("nosana", request, db)


@router.post("/config/providers/nosana/keys")
async def add_nosana_api_key_legacy(
    key_data: ProviderCredentialCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Legacy endpoint - redirects to universal credential system."""
    return await add_provider_credential("nosana", key_data, request, db)


@router.delete("/config/providers/nosana/keys/{key_name}")
async def delete_nosana_api_key(
    key_name: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Delete a nosana API key.
    Requires Admin role.
    """
    user_ctx = get_current_user_context(request)
    if "admin" not in user_ctx.roles:
        raise HTTPException(
            status_code=403, detail="Only admins can delete nosana API keys"
        )

    from inferia.services.api_gateway.management.config_manager import config_manager

    # 1. Cascade cleanup: Stop deployments and delete compute pools associated with this credential
    try:
        await _cleanup_provider_resources(
            "nosana", key_name, user_ctx.user_id, user_ctx.org_id
        )
    except Exception as e:
        logger.warning(f"Resource cleanup during Nosana key deletion failed: {e}")

    # Find and remove the key from raw config
    current_config = settings.providers.model_dump()
    api_keys = current_config.get("depin", {}).get("nosana", {}).get("api_keys", [])
    key_index = None
    for i, k in enumerate(api_keys):
        if k.get("name") == key_name:
            key_index = i
            break

    if key_index is None:
        raise HTTPException(
            status_code=404, detail=f"API key with name '{key_name}' not found"
        )

    # Remove the key
    api_keys.pop(key_index)

    db_config = {"providers": current_config}

    try:
        await config_manager.save_config(db, db_config)
        logger.info(f"Deleted nosana API key: {key_name}")
    except Exception as e:
        logger.error(f"Failed to delete nosana API key: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to delete API key: {e}")

    return {"status": "ok", "message": f"API key '{key_name}' deleted successfully"}


async def _cleanup_provider_resources(
    provider: str, credential_name: str, user_id: str, org_id: str
):
    """
    Orchestrate recursive deletion:
    1. Find compute pools using this credential.
    2. Terminate all deployments in those pools.
    3. Delete those compute pools.
    """
    orch_url = settings.orchestration_url or "http://localhost:8080"
    client = gateway_http_client.get_service_client()

    logger.info(
        f"Starting cascade cleanup for {provider} credential: {credential_name}"
    )

    # 1. List pools for this org/user
    # Some endpoints might use org_id, some might use owner_id.
    # In deployment_server.py: list_pools(owner_id: str | None = None)
    try:
        resp = await client.get(f"{orch_url}/deployment/listPools/{org_id}")
        if resp.status_code != 200:
            logger.error(f"Failed to list pools: {resp.status_code} {resp.text}")
            return

        pools_data = resp.json().get("pools", [])
    except Exception as e:
        logger.error(f"Error calling listPools: {e}")
        return

    # Filter pools matching provider and credential_name
    target_pools = [
        p
        for p in pools_data
        if p.get("provider") == provider
        and p.get("provider_credential_name") == credential_name
    ]

    if not target_pools:
        logger.info("No compute pools found for this credential. Skipping cleanup.")
        return

    for pool in target_pools:
        pool_id = pool["pool_id"]
        logger.info(f"Cleaning up pool {pool_id} ({pool.get('pool_name')})")

        # 2. Terminate deployments in this pool
        try:
            resp = await client.get(f"{orch_url}/deployment/listDeployments/{pool_id}")
            if resp.status_code == 200:
                deployments = resp.json().get("deployments", [])
                for dep in deployments:
                    dep_id = dep["deployment_id"]
                    if dep["state"] in ("STOPPED", "TERMINATED", "FAILED"):
                        logger.info(
                            f"Permanently deleting legacy deployment {dep_id} in pool {pool_id}"
                        )
                        await client.delete(f"{orch_url}/deployment/delete/{dep_id}")
                    else:
                        logger.info(f"Terminating deployment {dep_id} in pool {pool_id}")
                        await client.post(
                            f"{orch_url}/deployment/terminate",
                            json={"deployment_id": dep_id},
                        )
            else:
                logger.error(
                    f"Failed to list deployments for pool {pool_id}: {resp.status_code}"
                )
        except Exception as e:
            logger.error(f"Error terminating deployments for pool {pool_id}: {e}")

        # 3. Delete the pool
        try:
            logger.info(f"Deleting compute pool {pool_id}")
            await client.post(f"{orch_url}/deployment/deletepool/{pool_id}")
        except Exception as e:
            logger.error(f"Error deleting pool {pool_id}: {e}")

    logger.info(f"Cascade cleanup completed for {provider} credential: {credential_name}")

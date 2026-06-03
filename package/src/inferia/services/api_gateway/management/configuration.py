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
from inferia.services.api_gateway.schemas.auth import PermissionEnum
from inferia.services.api_gateway.rbac.authorization import authz_service
from inferia.services.api_gateway.audit.service import audit_service
from inferia.services.api_gateway.models import AuditLogCreate

# New imports for local provider config
from pydantic import BaseModel, Field
from pathlib import Path
import json
import os
from inferia.services.api_gateway.config import settings
from inferia.services.api_gateway.config import (
    ProvidersConfig,
    ProviderCredential,
    NosanaApiKeyEntry,
    CloudConfig,
    DePINConfig,
    VectorDBConfig,
    HuggingFaceConfig,
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
    hf_token_from_env: bool = False


def _mask_secret(value: Optional[str]) -> Optional[str]:
    if not value or len(value) < 8:
        return value  # Too short to mask meaningfully or empty
    return f"{value[:4]}...{value[-4:]}"


_FULL_MASK = "********"


def _is_masked(value: Optional[str]) -> bool:
    """True when value looks like one of our mask outputs.

    A frontend that round-trips the masked value back on save would
    otherwise overwrite the real credential. Both ``_mask_secret`` shapes
    are recognised: the full-mask ``********`` and the partial-mask
    ``XXXX...XXXX`` (4 chars + literal ``...`` + 4 chars, total 11).
    """
    if not value:
        return False
    if value == _FULL_MASK:
        return True
    if len(value) == 11 and value[4:7] == "..." and "*" not in value:
        return True
    return False


def _preserve_masked_secrets(incoming: dict, existing: dict) -> dict:
    """For each known sensitive field in ``incoming`` that arrives as a
    masked value, substitute the corresponding value from ``existing``.

    This is the server-side guard against the round-trip bug where a
    dashboard form pre-populated with masked values is submitted as-is,
    replacing the real key in the DB with the literal mask string.
    """
    # Operate on a copy so we don't mutate the caller's dict in place.
    import copy as _copy
    out = _copy.deepcopy(incoming)
    cloud_in = out.get("cloud") or {}
    cloud_ex = (existing.get("providers") or {}).get("cloud") or {}

    # AWS — both access_key_id and secret_access_key may arrive masked.
    aws_in = cloud_in.get("aws") or {}
    aws_ex = cloud_ex.get("aws") or {}
    for fld in ("access_key_id", "secret_access_key"):
        if _is_masked(aws_in.get(fld)):
            preserved = aws_ex.get(fld)
            if preserved is not None:
                aws_in[fld] = preserved
            else:
                # No prior value to fall back to; drop the masked field
                # entirely so we don't persist the literal mask string.
                aws_in.pop(fld, None)
    if aws_in:
        cloud_in["aws"] = aws_in

    # GCP service_account_json is masked with "********".
    gcp_in = cloud_in.get("gcp") or {}
    gcp_ex = cloud_ex.get("gcp") or {}
    if _is_masked(gcp_in.get("service_account_json")):
        if gcp_ex.get("service_account_json"):
            gcp_in["service_account_json"] = gcp_ex["service_account_json"]
        else:
            gcp_in.pop("service_account_json", None)
    if gcp_in:
        cloud_in["gcp"] = gcp_in

    # Azure client_secret.
    azure_in = cloud_in.get("azure") or {}
    azure_ex = cloud_ex.get("azure") or {}
    if _is_masked(azure_in.get("client_secret")):
        if azure_ex.get("client_secret"):
            azure_in["client_secret"] = azure_ex["client_secret"]
        else:
            azure_in.pop("client_secret", None)
    if azure_in:
        cloud_in["azure"] = azure_in

    # IBM api_key.
    ibm_in = cloud_in.get("ibm") or {}
    ibm_ex = cloud_ex.get("ibm") or {}
    if _is_masked(ibm_in.get("api_key")):
        if ibm_ex.get("api_key"):
            ibm_in["api_key"] = ibm_ex["api_key"]
        else:
            ibm_in.pop("api_key", None)
    if ibm_in:
        cloud_in["ibm"] = ibm_in

    if cloud_in:
        out["cloud"] = cloud_in

    # VectorDB chroma api_key.
    vec_in = (out.get("vectordb") or {}).get("chroma") or {}
    vec_ex = ((existing.get("providers") or {}).get("vectordb") or {}).get("chroma") or {}
    if _is_masked(vec_in.get("api_key")):
        if vec_ex.get("api_key"):
            vec_in["api_key"] = vec_ex["api_key"]
        else:
            vec_in.pop("api_key", None)

    # DePIN nosana wallet_private_key.
    nos_in = (out.get("depin") or {}).get("nosana") or {}
    nos_ex = ((existing.get("providers") or {}).get("depin") or {}).get("nosana") or {}
    if _is_masked(nos_in.get("wallet_private_key")):
        if nos_ex.get("wallet_private_key"):
            nos_in["wallet_private_key"] = nos_ex["wallet_private_key"]
        else:
            nos_in.pop("wallet_private_key", None)

    # HuggingFace token.
    hf_in = out.get("huggingface") or {}
    hf_ex = (existing.get("providers") or {}).get("huggingface") or {}
    if _is_masked(hf_in.get("token")):
        if hf_ex.get("token"):
            hf_in["token"] = hf_ex["token"]
        else:
            hf_in.pop("token", None)
    if hf_in:
        out["huggingface"] = hf_in

    return out


def _mask_config(config: ProvidersConfig) -> ProvidersConfig:
    # Create a copy to mask
    masked = config.model_copy(deep=True)

    # Cloud providers — nested under .cloud.*
    if masked.cloud.aws.secret_access_key:
        masked.cloud.aws.secret_access_key = "********"
    if masked.cloud.aws.access_key_id:
        masked.cloud.aws.access_key_id = _mask_secret(masked.cloud.aws.access_key_id)

    if masked.cloud.gcp.service_account_json:
        masked.cloud.gcp.service_account_json = "********"

    if masked.cloud.azure.client_secret:
        masked.cloud.azure.client_secret = "********"

    if masked.cloud.ibm.api_key:
        masked.cloud.ibm.api_key = _mask_secret(masked.cloud.ibm.api_key)

    # VectorDB — nested under .vectordb.chroma
    if masked.vectordb.chroma.api_key:
        masked.vectordb.chroma.api_key = "********"

    # DePIN — nested under .depin.nosana
    if masked.depin.nosana.wallet_private_key:
        masked.depin.nosana.wallet_private_key = "********"
    for entry in masked.depin.nosana.api_keys:
        if isinstance(entry, dict):
            if entry.get("key"):
                entry["key"] = _mask_secret(entry["key"])
        else:
            if entry.key:
                entry.key = _mask_secret(entry.key)

    # HuggingFace — nested under .huggingface
    if masked.huggingface.token:
        masked.huggingface.token = "********"

    return masked


def _require_policy_read_permission(user_ctx, deployment_id: Optional[str]) -> None:
    if deployment_id:
        authz_service.require_permission(user_ctx, PermissionEnum.DEPLOYMENT_LIST)
    else:
        authz_service.require_permission(user_ctx, PermissionEnum.ORG_VIEW)


def _require_policy_write_permission(user_ctx, deployment_id: Optional[str]) -> None:
    if deployment_id:
        authz_service.require_permission(user_ctx, PermissionEnum.DEPLOYMENT_UPDATE)
    else:
        authz_service.require_permission(user_ctx, PermissionEnum.ORG_UPDATE)


def _require_provider_read_permission(user_ctx) -> None:
    authz_service.require_permission(user_ctx, PermissionEnum.ORG_VIEW)


def _require_provider_write_permission(user_ctx) -> None:
    authz_service.require_permission(user_ctx, PermissionEnum.ORG_UPDATE)


@router.get("/config/providers", response_model=ProviderConfigResponse)
async def get_provider_config(request: Request, db: AsyncSession = Depends(get_db)):
    """
    Get current provider configuration. Returns masked secrets.
    Requires `organization:view`.
    """
    user_ctx = get_current_user_context(request)
    _require_provider_read_permission(user_ctx)

    masked_providers = _mask_config(settings.providers)
    hf_token_from_env = bool(os.getenv("INFERIA_HF_TOKEN"))
    return ProviderConfigResponse(providers=masked_providers, hf_token_from_env=hf_token_from_env)


@router.post("/config/providers")
async def update_provider_config(
    wrapper: ProviderConfigResponse,  # Expects { "providers": { ... } }
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Update provider configuration. Persists to the system database.
    Requires `organization:update`.
    """
    user_ctx = get_current_user_context(request)
    _require_provider_write_permission(user_ctx)

    # 1. Update DB and Local Cache
    from inferia.services.api_gateway.management.config_manager import config_manager

    new_data = wrapper.providers.model_dump(exclude_unset=True)

    # Defensive: if the dashboard round-trips masked values back on save
    # (e.g. user clicked Edit then Save without retyping), preserve the
    # existing secret instead of persisting the literal mask string.
    try:
        existing_config = await config_manager.load_config(db) or {}
    except Exception:
        existing_config = {}
    new_data = _preserve_masked_secrets(new_data, existing_config)

    # Structure for DB storage
    db_config = {"providers": new_data}

    try:
        await config_manager.save_config(db, db_config)

        # Log update status for providers (debug aid)
        if "cloud" in new_data:
            for prov in ("aws", "gcp", "azure", "ibm"):
                if prov in new_data.get("cloud", {}):
                    logger.debug("Config Update: cloud provider '%s' data received", prov)
        if "depin" in new_data:
            logger.debug("Config Update: depin data received")
    except Exception as e:
        logger.error("Failed to write config: %s", e)
        raise HTTPException(status_code=500, detail=f"Failed to save config: {e}")

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

    _require_policy_write_permission(user_ctx, config_data.deployment_id)

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
    await audit_service.log_event(
        db,
        AuditLogCreate(
            user_id=user_ctx.user_id,
            org_id=user_ctx.org_id,
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
    authz_service.require_permission(user_ctx, PermissionEnum.ORG_VIEW)
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
    _require_policy_read_permission(user_ctx, deployment_id)
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
        # Provide an explicit disabled default to avoid UI confusion. The base
        # default covers any remaining policy type (e.g. rate_limit).
        default_config = {"enabled": False}

        return ConfigResponse(
            policy_type=policy_type,
            config_json=default_config,
            updated_at=utcnow_naive(),
        )

    return policy


# --- Universal Provider Credential Management ---
# Works for ANY provider (nosana, akash, aws, etc.)


class ProviderCredentialCreate(BaseModel):
    name: str
    credential_type: str  # e.g., 'api_key', 'wallet', 'mnemonic', 'access_key'
    value: str


class ProviderCredentialUpdate(BaseModel):
    # `name` is redundant: the credential name is already in the URL path.
    # Kept optional for backward compatibility with older clients that send it.
    name: Optional[str] = None
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


# Extract nosana credentials from the typed in-memory settings
def _get_nosana_credentials_from_config() -> List[ProviderCredential]:
    """Extract nosana credentials from settings.providers.depin.nosana."""
    credentials = []
    nosana = settings.providers.depin.nosana

    if nosana.wallet_private_key:
        credentials.append(
            ProviderCredential(
                provider="nosana",
                name="wallet",
                credential_type="wallet_private_key",
                value=nosana.wallet_private_key,
                is_active=True,
            )
        )

    for entry in nosana.api_keys:
        if isinstance(entry, dict):
            key_val = entry.get("key", "")
            key_name = entry.get("name", "unnamed")
            is_active = entry.get("is_active", True)
        else:
            key_val = entry.key
            key_name = entry.name
            is_active = entry.is_active
        if key_val:
            credentials.append(
                ProviderCredential(
                    provider="nosana",
                    name=key_name,
                    credential_type="api_key",
                    value=key_val,
                    is_active=is_active,
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
    Requires `organization:view`.
    Works for ANY provider: nosana, akash, aws, etc.
    """
    user_ctx = get_current_user_context(request)
    _require_provider_read_permission(user_ctx)

    # TODO: In production, fetch from provider_credentials table
    # For now, use config-based storage with migration
    credentials = []

    if provider == "nosana":
        credentials = _get_nosana_credentials_from_config()
    elif provider == "aws":
        aws = settings.providers.cloud.aws
        if aws.access_key_id:
            credentials.append(ProviderCredential(
                provider="aws", name="default", credential_type="access_key_id",
                value=aws.access_key_id, is_active=True,
            ))
        if aws.secret_access_key:
            credentials.append(ProviderCredential(
                provider="aws", name="default", credential_type="secret_access_key",
                value=aws.secret_access_key, is_active=True,
            ))
    elif provider == "gcp":
        gcp = settings.providers.cloud.gcp
        if gcp.project_id:
            credentials.append(ProviderCredential(
                provider="gcp", name="default", credential_type="project_id",
                value=gcp.project_id, is_active=True,
            ))
        if gcp.service_account_json:
            credentials.append(ProviderCredential(
                provider="gcp", name="default", credential_type="service_account_json",
                value=gcp.service_account_json, is_active=True,
            ))
    elif provider == "azure":
        az = settings.providers.cloud.azure
        if az.subscription_id:
            credentials.append(ProviderCredential(
                provider="azure", name="default", credential_type="subscription_id",
                value=az.subscription_id, is_active=True,
            ))
        if az.tenant_id:
            credentials.append(ProviderCredential(
                provider="azure", name="default", credential_type="tenant_id",
                value=az.tenant_id, is_active=True,
            ))
        if az.client_id:
            credentials.append(ProviderCredential(
                provider="azure", name="default", credential_type="client_id",
                value=az.client_id, is_active=True,
            ))
        if az.client_secret:
            credentials.append(ProviderCredential(
                provider="azure", name="default", credential_type="client_secret",
                value=az.client_secret, is_active=True,
            ))
    elif provider == "ibm":
        ibm = settings.providers.cloud.ibm
        if ibm.api_key:
            credentials.append(ProviderCredential(
                provider="ibm", name="default", credential_type="api_key",
                value=ibm.api_key, is_active=True,
            ))
        if ibm.resource_group_id:
            credentials.append(ProviderCredential(
                provider="ibm", name="default", credential_type="resource_group_id",
                value=ibm.resource_group_id, is_active=True,
            ))
    # Unknown providers (chroma, groq, lakera, akash, etc.) return an empty list

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
    Requires `organization:update`.

    Examples:
    - provider="nosana", credential_type="api_key"
    - provider="akash", credential_type="mnemonic"
    - provider="aws", credential_type="access_key"
    """
    user_ctx = get_current_user_context(request)
    _require_provider_write_permission(user_ctx)

    from inferia.services.api_gateway.management.config_manager import config_manager

    # Build new credential
    new_credential = ProviderCredential(
        provider=provider,
        name=credential_data.name,
        credential_type=credential_data.credential_type,
        value=credential_data.value,
        is_active=True,
    )

    # Get current config as nested dict
    current_config = settings.providers.model_dump()

    # Store based on provider type
    if provider == "nosana":
        depin = current_config.setdefault("depin", {})
        nosana = depin.setdefault("nosana", {})
        nosana.setdefault("api_keys", [])

        # Check for duplicate names
        existing_names = {k["name"] for k in nosana["api_keys"]}
        if credential_data.name in existing_names:
            raise HTTPException(
                status_code=400,
                detail=f"Credential with name '{credential_data.name}' already exists for {provider}",
            )

        nosana["api_keys"].append(
            {
                "name": new_credential.name,
                "key": new_credential.value,
                "is_active": new_credential.is_active,
            }
        )
    elif provider in ("aws", "gcp", "azure", "ibm"):
        cloud = current_config.setdefault("cloud", {})
        prov_cfg = cloud.setdefault(provider, {})
        prov_cfg[credential_data.credential_type] = credential_data.value
    else:
        raise HTTPException(
            status_code=400,
            detail=f"Provider '{provider}' not supported. Valid: aws, gcp, azure, ibm, nosana",
        )

    db_config = {"providers": current_config}

    try:
        await config_manager.save_config(db, db_config)
        logger.info(f"Added new credential for {provider}: {credential_data.name}")
    except Exception as e:
        logger.error(f"Failed to add credential: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to save credential: {e}")

    await audit_service.log_event(
        db,
        AuditLogCreate(
            user_id=user_ctx.user_id,
            org_id=user_ctx.org_id,
            action="credential.create",
            resource_type="credential",
            resource_id=credential_data.name,
            details={"provider": provider, "credential_type": credential_data.credential_type},
            status="success",
        ),
    )

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
    Requires `organization:update`.
    """
    user_ctx = get_current_user_context(request)
    _require_provider_write_permission(user_ctx)

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
    elif provider in ("aws", "gcp", "azure", "ibm"):
        prov_cfg = current_config.get("cloud", {}).get(provider, {})
        if credential_data.credential_type and credential_data.credential_type in prov_cfg:
            if credential_data.value is not None:
                prov_cfg[credential_data.credential_type] = credential_data.value
            updated = True

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

    await audit_service.log_event(
        db,
        AuditLogCreate(
            user_id=user_ctx.user_id,
            org_id=user_ctx.org_id,
            action="credential.update",
            resource_type="credential",
            resource_id=credential_name,
            details={"provider": provider},
            status="success",
        ),
    )

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
    Requires `organization:update`.
    """
    user_ctx = get_current_user_context(request)
    _require_provider_write_permission(user_ctx)

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
    elif provider in ("aws", "gcp", "azure", "ibm"):
        prov_cfg = current_config.get("cloud", {}).get(provider, {})
        if credential_name in prov_cfg:
            del prov_cfg[credential_name]
            deleted = True

    if not deleted:
        raise HTTPException(
            status_code=404,
            detail=f"Credential '{credential_name}' not found for {provider}",
        )

    db_config = {"providers": current_config}

    try:
        await config_manager._force_replace_config(db, db_config)
        logger.info(f"Deleted credential for {provider}: {credential_name}")
    except Exception as e:
        logger.error(f"Failed to delete credential: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to delete credential: {e}")

    await audit_service.log_event(
        db,
        AuditLogCreate(
            user_id=user_ctx.user_id,
            org_id=user_ctx.org_id,
            action="credential.delete",
            resource_type="credential",
            resource_id=credential_name,
            details={"provider": provider},
            status="success",
        ),
    )

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
    Requires `organization:update`.
    """
    user_ctx = get_current_user_context(request)
    _require_provider_write_permission(user_ctx)

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
                        logger.info(
                            f"Terminating deployment {dep_id} in pool {pool_id}"
                        )
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

        # 3. Stop the pool first
        try:
            logger.info(f"Stopping compute pool {pool_id}")
            stop_resp = await client.post(f"{orch_url}/deployment/stoppool/{pool_id}")
            if stop_resp.status_code not in (200, 202):
                logger.error(
                    f"Failed to stop pool {pool_id}: {stop_resp.status_code} {stop_resp.text}"
                )
                continue
        except Exception as e:
            logger.error(f"Error stopping pool {pool_id}: {e}")
            continue

        # 4. Wait until pool reaches terminated lifecycle state
        terminated = False
        for _ in range(30):
            await asyncio.sleep(2)
            try:
                pool_resp = await client.get(f"{orch_url}/deployment/pool/{pool_id}")
                if pool_resp.status_code != 200:
                    continue

                lifecycle_state = (
                    pool_resp.json().get("lifecycle_state", "running").lower()
                )
                if lifecycle_state == "terminated":
                    terminated = True
                    break
            except Exception:
                continue

        if not terminated:
            logger.error(
                f"Timed out waiting for pool {pool_id} to reach terminated state"
            )
            continue

        # 5. Delete terminated pool
        try:
            logger.info(f"Deleting compute pool {pool_id}")
            delete_resp = await client.post(
                f"{orch_url}/deployment/deletepool/{pool_id}"
            )
            if delete_resp.status_code not in (200, 202):
                logger.error(
                    f"Failed to delete pool {pool_id}: {delete_resp.status_code} {delete_resp.text}"
                )
        except Exception as e:
            logger.error(f"Error deleting pool {pool_id}: {e}")

    logger.info(
        f"Cascade cleanup completed for {provider} credential: {credential_name}"
    )

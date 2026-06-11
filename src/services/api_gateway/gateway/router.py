"""
API Gateway router for inference endpoints.
Handles request routing to the orchestration layer.
"""

from typing import Any, Dict, List, Optional

from services.api_gateway.db.database import get_db
from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Request,
    Response,
    status,
    BackgroundTasks,
    Query,
)
from services.api_gateway.models import (
    InferenceRequest,
    InferenceResponse,
    ModelInfo,
    ModelsListResponse,
)
from services.api_gateway.rbac.router import router as auth_router
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from services.api_gateway.db.models import Deployment

from services.api_gateway.gateway.rate_limiter import rate_limiter
from services.api_gateway.gateway.http_client import gateway_http_client
from services.api_gateway.security.encryption import LogEncryption
from services.api_gateway.config import settings
import httpx
import asyncio
import cachetools


import logging

logger = logging.getLogger(__name__)

encryption_service = None
encryption_available = False

if settings.log_encryption_key:
    try:
        encryption_service = LogEncryption(settings.log_encryption_key)
        encryption_available = True
    except Exception as e:
        logger.critical(f"Failed to initialize log encryption: {e}")
        raise RuntimeError(
            f"Log encryption key provided but initialization failed: {e}"
        )
elif settings.is_production:
    logger.warning(
        "LOG_ENCRYPTION_KEY not set in production! Inference logs will NOT be encrypted. "
        "Set LOG_ENCRYPTION_KEY environment variable to enable encryption."
    )

router = APIRouter(prefix="/internal", tags=["Internal Inference"])
router.include_router(auth_router)


# --- Policy Engine: Internal Endpoints ---
from services.api_gateway.policy.engine import policy_engine
from pydantic import BaseModel


class QuotaCheckRequest(BaseModel):
    user_id: str
    model: str = "default"


class UsageTrackRequest(BaseModel):
    user_id: str
    model: str
    usage: Dict[str, int]  # prompt_tokens, completion_tokens, total_tokens


@router.post("/policy/check_quota")
async def check_user_quota(
    request: QuotaCheckRequest, db: AsyncSession = Depends(get_db)
):
    """
    Check if user has sufficient quota.
    Raises 429 if exceeded.
    """
    await policy_engine.check_quota(db, request.user_id, request.model)
    return {"status": "ok", "message": "Quota within limits"}


@router.post("/policy/track_usage")
async def track_user_usage(
    request: UsageTrackRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """
    Increment user usage stats.
    Uses Redis for real-time tracking and background task for Postgres persistence.
    """
    # 1. Immediate Redis update for quota enforcement
    await policy_engine.increment_redis_only(
        request.user_id, request.model, request.usage
    )

    # 2. Background DB persistence
    background_tasks.add_task(
        policy_engine.persist_usage_db,
        db,
        request.user_id,
        request.model,
        request.usage,
    )

    return {"status": "ok", "message": "Usage tracking initiated"}


# --- Inference Logging ---
import uuid

from services.api_gateway.db.models import InferenceLog
from services.api_gateway.models import InferenceLogCreate


async def _persist_log_background(log_data: InferenceLogCreate, log_id: str):
    """
    Background task to persist inference log.

    Creates its own DB session instead of reusing the request-scoped one,
    which FastAPI closes before the background task runs.
    """
    from services.api_gateway.db.database import AsyncSessionLocal

    try:
        async with AsyncSessionLocal() as db:
            # Handle encryption based on availability
            if encryption_available and encryption_service and log_data.request_payload:
                request_payload = {
                    "encrypted": True,
                    "ciphertext": encryption_service.encrypt(log_data.request_payload),
                }
            elif log_data.request_payload:
                logger.warning(
                    f"Storing inference log {log_id} WITHOUT encryption - "
                    "LOG_ENCRYPTION_KEY not configured"
                )
                request_payload = {
                    "encrypted": False,
                    "plaintext": log_data.request_payload,
                }
            else:
                request_payload = None

            log = InferenceLog(
                id=log_id,
                deployment_id=log_data.deployment_id,
                user_id=log_data.user_id,
                ip_address=log_data.ip_address,
                model=log_data.model,
                request_payload=request_payload,
                latency_ms=log_data.latency_ms,
                ttft_ms=log_data.ttft_ms,
                tokens_per_second=log_data.tokens_per_second,
                prompt_tokens=log_data.prompt_tokens,
                completion_tokens=log_data.completion_tokens,
                total_tokens=log_data.total_tokens,
                status_code=log_data.status_code,
                error_message=log_data.error_message,
                is_streaming=log_data.is_streaming,
                applied_policies=log_data.applied_policies,
            )
            db.add(log)
            await db.commit()
    except Exception as e:
        logger.error(f"Failed to persist inference log in background: {e}")


@router.post("/logs/create")
async def create_inference_log(
    log_data: InferenceLogCreate,
    background_tasks: BackgroundTasks,
):
    """
    Create an inference log entry.
    Offloaded to background task for performance.
    """
    log_id = str(uuid.uuid4())
    background_tasks.add_task(_persist_log_background, log_data, log_id)
    return {"status": "ok", "log_id": log_id}


# In-memory cache for models list (30s TTL)
models_cache = cachetools.TTLCache(maxsize=100, ttl=30)


@router.get("/models", response_model=ModelsListResponse)
async def list_models(
    request: Request,
    skip: int = Query(0, ge=0, description="Number of models to skip"),
    limit: int = Query(
        50, ge=1, le=100, description="Maximum number of models to return"
    ),
    db: AsyncSession = Depends(get_db),
):
    """
    List available models with pagination.
    """
    # Check rate limit
    await rate_limiter.check_rate_limit(request)

    # 1. Validate API Key
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid Authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )

    api_key = auth_header.split(" ")[1]
    key_record = await policy_engine.verify_api_key(db, api_key)

    if not key_record:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API Key",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Check cache after authentication (include org_id to avoid cross-org leaks)
    cache_key = (key_record.org_id, skip, limit)
    if cache_key in models_cache:
        return models_cache[cache_key]

    # Get available models from database (real deployments) with pagination
    # Only return models that are in a 'running' state (READY or RUNNING)
    result = await db.execute(
        select(Deployment)
        .where(Deployment.state.in_(["RUNNING", "READY", "ready"]))
        .offset(skip)
        .limit(limit)
    )
    all_deployments = result.scalars().all()

    # Health Check for Nosana deployments
    async def check_health(
        client: httpx.AsyncClient, d: Deployment
    ) -> Optional[Deployment]:
        # Perform health check for Nosana and various inference engines
        # Often Nosana deployments use vllm, ollama, etc.
        is_nosana_link = d.endpoint and "nos.ci" in d.endpoint
        supported_engines = ["nosana", "vllm", "vllm-omni", "ollama", "triton"]
        if d.engine not in supported_engines and not is_nosana_link:
            return d

        if (
            not d.endpoint
            or not d.endpoint.startswith("http")
            or d.endpoint == "job-running-confidential"
        ):
            return None

        try:
            # 1. Resolve API key for health check
            provider_key = None
            if d.configuration:
                config = d.configuration
                # configuration is automatically decrypted JSON from EncryptedJSON column
                provider_key = (
                    config.get("api_key") or config.get("key") or config.get("token")
                )

            # Fallback to internal key if no specific key provided for Depin engine
            if not provider_key:
                provider_key = settings.internal_api_key

            headers = {}
            if provider_key:
                headers["Authorization"] = f"Bearer {provider_key}"

            # 2. Perform health check to /v1/models as requested
            health_url = f"{d.endpoint.rstrip('/')}/v1/models"
            resp = await client.get(health_url, headers=headers, timeout=5.0)

            # Validate status code AND that response is valid JSON
            if resp.status_code == 200:
                try:
                    data = resp.json()
                    if isinstance(data, dict):
                        return d
                    else:
                        logger.warning(
                            f"Health check failed for {d.model_name}: Response is JSON but not a dictionary"
                        )
                except Exception:
                    logger.warning(
                        f"Health check failed for {d.model_name}: Invalid JSON response"
                    )
            else:
                logger.warning(
                    f"Health check failed for {d.model_name}: Status {resp.status_code}"
                )
        except Exception as e:
            logger.warning(
                f"Health check failed for Nosana model {d.model_name} at {d.endpoint}: {e}"
            )

        return None

    # Run health checks in parallel with bounded concurrency
    _health_semaphore = asyncio.Semaphore(10)

    async def _bounded_check(client, d):
        async with _health_semaphore:
            return await check_health(client, d)

    client = gateway_http_client.get_service_client()
    health_results = await asyncio.gather(
        *(_bounded_check(client, d) for d in all_deployments), return_exceptions=True
    )

    deployments = [
        d for d in health_results if isinstance(d, Deployment) and d is not None
    ]

    mock_models = [
        ModelInfo(
            id=str(d.model_name),
            model_name=str(d.model_name),
            created=int(d.created_at.timestamp()) if d.created_at is not None else 0,
            owned_by=str(d.org_id) if d.org_id is not None else "system",
            description=f"Model deployment for {d.model_name} ({d.engine})",
        )
        for d in deployments
    ]

    response = ModelsListResponse(data=mock_models)

    # Cache the response
    models_cache[cache_key] = response

    return response


# NEW: Context Resolution for Inference Gateway
from services.api_gateway.db.database import get_db
from fastapi import Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select


class ResolveContextRequest(BaseModel):
    api_key: str
    model: str
    model_type: str = "inference"
    sandbox: bool = False


from typing import Any, Dict, Optional


class ResolveContextResponse(BaseModel):
    valid: bool
    error: Optional[str] = None
    deployment: Optional[Dict] = None
    rate_limit_config: Optional[Dict] = None
    user_id_context: Optional[str] = None
    org_id: Optional[str] = None
    log_payloads: bool = True


@router.post("/context/resolve", response_model=ResolveContextResponse)
async def resolve_inference_context(
    request: ResolveContextRequest, db: AsyncSession = Depends(get_db)
):
    """
    Resolve inference context from API Key and Model.
    Used by Inference Gateway to fetch config.
    """
    # Delegate to Policy Engine
    result = await policy_engine.resolve_context(
        db, request.api_key, request.model, request.model_type, request.sandbox
    )

    if not result["valid"]:
        return ResolveContextResponse(valid=False, error=result["error"])

    deployment = result["deployment"]
    config = result["config"]
    user_id_context = result["user_id_context"]

    return ResolveContextResponse(
        valid=True,
        deployment={
            "id": deployment["id"],
            "model_name": deployment["model_name"],
            "endpoint": deployment["endpoint"],
            "engine": deployment["engine"],
            "configuration": deployment["configuration"],
            "inference_model": deployment.get("inference_model"),
            # Pass the pool inference_token through to the inference data plane
            # so it can auth to a worker-hosted deploy's :8080 proxy. This is
            # the internal resolve channel (internal-API-key gated), never the
            # dashboard-facing deployment API.
            "inference_token": deployment.get("inference_token"),
        },
        rate_limit_config=config.get("rate_limit"),
        user_id_context=user_id_context,
        org_id=deployment["org_id"],
        log_payloads=result.get("log_payloads", True),
    )


@router.get("/config/provider")
async def get_provider_config_internal(request: Request):
    """
    Internal endpoint for sidecars to fetch masked provider config.
    Protected by Internal API Key (via middleware).
    Only returns non-sensitive configuration - secrets/keys are masked.
    """

    def mask_credentials(config: dict) -> dict:
        """Recursively mask sensitive fields in provider config."""
        if not isinstance(config, dict):
            return config

        masked = {}
        sensitive_keys = {
            "key",
            "api_key",
            "secret",
            "secret_access_key",
            "password",
            "mnemonic",
            "token",
            "credential",
        }

        for k, v in config.items():
            if k.lower() in sensitive_keys:
                if v and isinstance(v, str) and len(v) > 4:
                    masked[k] = v[:4] + "****"
                else:
                    masked[k] = "****"
            elif isinstance(v, dict):
                masked[k] = mask_credentials(v)
            elif isinstance(v, list):
                masked[k] = [
                    mask_credentials(item) if isinstance(item, dict) else item
                    for item in v
                ]
            else:
                masked[k] = v

        return masked

    providers = settings.providers.model_dump()
    masked_providers = mask_credentials(providers)

    return {"providers": masked_providers}


@router.get("/config/credentials")
async def get_provider_credentials_internal(request: Request):
    """
    Internal endpoint for sidecar services to fetch UNMASKED credentials.
    REQUIRES valid X-Internal-API-Key header - only for service-to-service auth.
    This endpoint should ONLY be called by trusted internal services.
    """
    # Double-check internal API key is present and valid
    api_key = request.headers.get("X-Internal-API-Key") or request.headers.get(
        "X-Internal-Key"
    )
    if not api_key or api_key != settings.internal_api_key:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid or missing internal API key",
        )

    # Return unmasked credentials only to authenticated internal services
    return {"providers": settings.providers.model_dump()}

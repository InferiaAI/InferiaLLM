"""
API Gateway router for inference endpoints.
Handles request routing to the orchestration layer.
"""

from typing import Any, Dict, List, Optional

from inferia.services.api_gateway.db.database import get_db
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
from inferia.common.schemas.guardrail import GuardrailScanRequest, ScanType
from inferia.services.api_gateway.models import (
    InferenceRequest,
    InferenceResponse,
    ModelInfo,
    ModelsListResponse,
)
from inferia.services.api_gateway.rbac.router import router as auth_router
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from inferia.services.api_gateway.db.models import Deployment

from inferia.services.api_gateway.gateway.rate_limiter import rate_limiter
from inferia.services.api_gateway.security.encryption import LogEncryption
from inferia.services.api_gateway.config import settings
import httpx
import asyncio
import cachetools


import logging

logger = logging.getLogger(__name__)

encryption_service = None
if settings.log_encryption_key:
    try:
        encryption_service = LogEncryption(settings.log_encryption_key)
    except Exception as e:
        logger.critical(f"Failed to initialize log encryption: {e}")
        raise RuntimeError(
            f"Log encryption key provided but initialization failed: {e}"
        )

router = APIRouter(prefix="/internal", tags=["Internal Inference"])
router.include_router(auth_router)


# --- Policy Engine: Internal Endpoints ---
from inferia.services.api_gateway.policy.engine import policy_engine
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

from inferia.services.api_gateway.db.models import InferenceLog
from inferia.services.api_gateway.models import InferenceLogCreate


async def _persist_log_background(
    db: AsyncSession, log_data: InferenceLogCreate, log_id: str
):
    """Background task to persist inference log."""
    try:
        log = InferenceLog(
            id=log_id,
            deployment_id=log_data.deployment_id,
            user_id=log_data.user_id,
            ip_address=log_data.ip_address,
            model=log_data.model,
            request_payload=(
                {
                    "encrypted": True,
                    "ciphertext": encryption_service.encrypt(log_data.request_payload),
                }
                if encryption_service and log_data.request_payload
                else log_data.request_payload
            ),
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
    db: AsyncSession = Depends(get_db),
):
    """
    Create an inference log entry.
    Offloaded to background task for performance.
    """
    log_id = str(uuid.uuid4())
    background_tasks.add_task(_persist_log_background, db, log_data, log_id)
    return {"status": "ok", "log_id": log_id}


@router.post("/guardrails/scan", response_model=dict)
async def scan_content(request_body: GuardrailScanRequest, request: Request):
    """
    Directly access the guardrail scanner for testing or external checks.
    Proxies to Guardrail Microservice.
    """
    # Check rate limit
    await rate_limiter.check_rate_limit(request)

    # Get authenticated user (if available) or use context from body
    user_id = "unknown"

    # Use UserContext from middleware (Pydantic model, safe)
    if hasattr(request.state, "user"):
        user = request.state.user
        user_id = user.user_id
    elif hasattr(request_body, "user_id") and request_body.user_id:
        # Internal M2M call
        user_id = request_body.user_id

    # Prepare payload for Guardrail Service
    payload = request_body.model_dump()
    payload["user_id"] = user_id

    # We map "text" from request to the service expected input
    # GuardrailScanRequest has 'text'. Service expects 'text'.

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{settings.guardrail_service_url}/scan",
                json=payload,
                timeout=settings.guardrail_settings_timeout
                if hasattr(settings, "guardrail_settings_timeout")
                else 10.0,
            )
            response.raise_for_status()
            return response.json()
    except Exception as e:
        logger.error(f"Guardrail Service call failed: {e}")
        # Fail open or closed? Usually closed for security.
        raise HTTPException(status_code=500, detail=f"Guardrail check failed: {str(e)}")

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
    
    # Check cache after authentication
    cache_key = (skip, limit)
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
    async def check_health(client: httpx.AsyncClient, d: Deployment) -> Optional[Deployment]:
        # Perform health check for Nosana and various inference engines
        # Often Nosana deployments use vllm, ollama, etc.
        is_nosana_link = d.endpoint and "nos.ci" in d.endpoint
        supported_engines = ["nosana", "vllm", "vllm-omni", "ollama", "triton"]
        if d.engine not in supported_engines and not is_nosana_link:
            return d

        if not d.endpoint or not d.endpoint.startswith("http") or d.endpoint == "job-running-confidential":
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

    # Run health checks in parallel
    async with httpx.AsyncClient() as client:
        health_results = await asyncio.gather(
            *(check_health(client, d) for d in all_deployments),
            return_exceptions=True
        )
    
    deployments = [
        d for d in health_results 
        if isinstance(d, Deployment) and d is not None
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
from inferia.services.api_gateway.db.database import get_db
from fastapi import Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select


class ResolveContextRequest(BaseModel):
    api_key: str
    model: str


from typing import Any, Dict, Optional


class ResolveContextResponse(BaseModel):
    valid: bool
    error: Optional[str] = None
    deployment: Optional[Dict] = None
    guardrail_config: Optional[Dict] = None
    rag_config: Optional[Dict] = None
    template_config: Optional[Dict] = None
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
    result = await policy_engine.resolve_context(db, request.api_key, request.model)

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
        },
        guardrail_config=config["guardrail"],
        rag_config=config["rag"],
        template_config=config.get("prompt_template"),
        rate_limit_config=config.get("rate_limit"),
        user_id_context=user_id_context,
        org_id=deployment["org_id"],
        log_payloads=result.get("log_payloads", True),
    )


# --- Prompt Engine Integration ---
from inferia.services.api_gateway.models import (
    Message,
    PromptProcessRequest,
    PromptProcessResponse,
)


@router.post("/prompt/process", response_model=PromptProcessResponse)
async def process_prompt(
    request: PromptProcessRequest,
    # Internal endpoint, simpler auth or rely on firewall/internal network
    db: AsyncSession = Depends(get_db),
):
    """
    Process a prompt: Rewrite -> RAG -> Template.
    Returns the modified messages list.
    """
    messages = request.messages
    org_id = request.org_id
    if not messages:
        return PromptProcessResponse(messages=[])

    # 1. Identify User Query (Last User Message)
    user_msg_idx = -1
    for i, m in enumerate(reversed(messages)):
        if m.role == "user":
            user_msg_idx = len(messages) - 1 - i
            break

    if user_msg_idx == -1:
        # No user message, return as is
        return PromptProcessResponse(messages=messages)

    original_query = messages[user_msg_idx].content
    processed_query = original_query

    # 2. Rewrite (Disabled)
    rewritten = False
    # Rewriting functionality removed as per request.

    # 4. Advanced Templating with Variable Mapping
    used_template_id = request.template_id
    template_content = request.template_content

    # If config provides a base template, prefer that, unless override is present
    template_config = request.template_config or {}
    rag_used = False  # Initialize for scope

    # Only use template config if explicitly enabled
    template_enabled = template_config.get("enabled", False)

    if not used_template_id and template_enabled:
        used_template_id = template_config.get("base_template_id")
        # If content override is present in config, use it
        if template_config.get("content"):
            template_content = template_config.get("content")
            used_template_id = used_template_id or "custom_override"

    # If we have a template to work with AND templates are enabled
    if (used_template_id or template_content) and template_enabled:
        variables = request.template_vars or {}
        # Only use variable_mapping if template is enabled via config
        variable_mapping = (
            template_config.get("variable_mapping", {}) if template_enabled else {}
        )

        async with httpx.AsyncClient() as client:
            # Resolve mapped variables
            for var_name, config in variable_mapping.items():
                source = config.get("source")
                if source == "rag":
                    collection = config.get("collection_id") or "default"
                    top_k = config.get("top_k", 3)
                    # Fetch RAG context for this variable via Data Service (Prompt Engine)
                    try:
                        resp = await client.post(
                            f"{settings.data_service_url}/context/assemble",
                            json={
                                "query": processed_query,
                                "collection_name": collection,
                                "org_id": org_id or "default",
                                "n_results": top_k,
                            },
                            timeout=5.0,
                        )
                        if resp.status_code == 200:
                            rag_val = resp.json().get("context", "")
                            if rag_val:
                                variables[var_name] = rag_val
                                rag_used = True
                    except Exception as e:
                        logger.error(f"Failed to fetch RAG context: {e}")

                elif source == "static":
                    variables[var_name] = config.get("value", "")
                elif source == "request":
                    # Key alias, e.g. map "user_name" var to "user" payload key
                    key = config.get("key", var_name)
                    # If key exists in request vars, use it. Otherwise keep existing or ignore.
                    if key in variables:
                        variables[var_name] = variables[key]

            # Always inject standard vars if not mapped (backward compat)
            if "query" not in variables:
                variables["query"] = processed_query

            # Legacy RAG support (if rag_config passed but no mapping used)
            if (
                request.rag_config
                and request.rag_config.get("enabled")
                and not rag_used
            ):
                if "context" not in variables:
                    collection = (
                        request.rag_config.get("default_collection") or "default"
                    )
                    top_k = request.rag_config.get("top_k", 3)
                    try:
                        resp = await client.post(
                            f"{settings.data_service_url}/context/assemble",
                            json={
                                "query": processed_query,
                                "collection_name": collection,
                                "org_id": org_id or "default",
                                "n_results": top_k,
                            },
                            timeout=5.0,
                        )
                        if resp.status_code == 200:
                            rag_ctx = resp.json().get("context", "")
                            if rag_ctx:
                                variables["context"] = rag_ctx
                                rag_used = True
                    except Exception as e:
                        logger.error(f"Failed to fetch legacy RAG context: {e}")

            # Render Template via Data Service (Prompt Engine)
            try:
                process_payload = {
                    "variables": variables,
                    "template_id": used_template_id if not template_content else None,
                    "template_content": template_content,
                }

                resp = await client.post(
                    f"{settings.data_service_url}/process",
                    json=process_payload,
                    timeout=2.0,
                )

                if resp.status_code == 200:
                    system_content = resp.json().get("content", "")

                    # Replace or Insert System Message
                    messages = [m for m in messages if m.role != "system"]
                    messages.insert(0, Message(role="system", content=system_content))
                else:
                    logger.error(f"Data Service Process failed: {resp.text}")

            except Exception as e:
                logger.error(f"Failed to call Data Service Process: {e}")

    # Fallback RAG Logic (No Template, but RAG Enabled)
    elif not rag_used and request.rag_config and request.rag_config.get("enabled"):
        collection = request.rag_config.get("default_collection") or "default"
        top_k = request.rag_config.get("top_k", 3)

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{settings.data_service_url}/context/assemble",
                    json={
                        "query": processed_query,
                        "collection_name": collection,
                        "org_id": org_id or "default",
                        "n_results": top_k,
                    },
                    timeout=5.0,
                )
                if resp.status_code == 200:
                    rag_context = resp.json().get("context", "")
                    if rag_context:
                        rag_used = True
                        # Strategy: Append to User Message (easiest for non-template flows)
                        context_msg = f"Context Information:\n{rag_context}\n\n"
                        messages[user_msg_idx].content = context_msg + processed_query
        except Exception as e:
            logger.error(f"Failed to fetch fallback RAG context: {e}")

    return PromptProcessResponse(
        messages=messages,
        used_template_id=used_template_id,
        rewritten=rewritten,
        rag_context_used=rag_used,
    )


@router.get("/config/provider")
async def get_provider_config_internal(request: Request):
    """
    Internal endpoint for sidecars to fetch UNMASKED provider config.
    Protected by Internal API Key (via middleware).
    """
    # Return the full unmasked config from memory (decrypted by Pydantic/DB load)
    return {"providers": settings.providers.model_dump()}

from fastapi import APIRouter, HTTPException, Query, WebSocket, WebSocketDisconnect, Request
import logging

logger = logging.getLogger(__name__)
from pydantic import BaseModel
import asyncpg
import collections
import grpc
import json
import asyncio
import aiohttp
from uuid import UUID

from inferia.services.orchestration.v1 import (
    model_deployment_pb2,
    model_deployment_pb2_grpc,
    model_registry_pb2,
    model_registry_pb2_grpc,
    compute_pool_pb2,
    compute_pool_pb2_grpc,
)

from inferia.services.orchestration.repositories.provider_repo import (
    ProviderResourceRepository,
)
from inferia.services.orchestration.services.adapter_engine.registry import (
    get_adapter,
    ADAPTER_REGISTRY,
)
from inferia.services.orchestration.services.model_deployment.log_store import (
    DeploymentLogStore,
    DeploymentLogBuffer,
)
from inferia.services.orchestration.config import settings as orch_settings
from typing import Any, Optional

import os



def _resolve_postgres_dsn() -> str:
    """Return a raw asyncpg-compatible DSN.

    Prefers POSTGRES_DSN; falls back to DATABASE_URL (stripping a SQLAlchemy
    +asyncpg driver prefix if present). Last resort is a dev-localhost default.
    """
    dsn = os.getenv("POSTGRES_DSN")
    if dsn:
        return dsn
    dsn = os.getenv("DATABASE_URL")
    if dsn:
        return dsn.replace("postgresql+asyncpg://", "postgresql://", 1)
    return "postgresql://inferia:inferia@localhost:5432/inferia"


POSTGRES_DSN = _resolve_postgres_dsn()
GRPC_ADDR = os.getenv("ORCHESTRATION_GRPC_ADDR", "127.0.0.1:50051")
NOSANA_SIDECAR_URL = os.getenv("NOSANA_SIDECAR_URL", "http://localhost:3000")
NOSANA_CLIENT_MANAGER_URL = os.getenv(
    "NOSANA_CLIENT_MANAGER_URL", "https://client-manager.k8s.prd.nosana.com"
)
NOSANA_INGRESS_DOMAIN = os.getenv("NOSANA_INGRESS_DOMAIN", "node.k8s.prd.nos.ci")

# Singleton log store — initialized lazily on first use
_log_store: Optional[DeploymentLogStore] = None


async def _get_log_store() -> DeploymentLogStore:
    """Get or initialize the deployment log store singleton."""
    global _log_store
    if _log_store is None:
        _log_store = DeploymentLogStore(
            elasticsearch_url=orch_settings.elasticsearch_url
        )
        await _log_store.initialize()
    return _log_store


async def _create_log_buffer(deployment_id: str, org_id: str) -> DeploymentLogBuffer:
    """Create a log buffer for a WebSocket session, seeded with ES line count."""
    store = await _get_log_store()
    start_line = await store.get_max_line_number(deployment_id)
    return DeploymentLogBuffer(
        store=store,
        deployment_id=deployment_id,
        org_id=org_id,
        max_lines=orch_settings.deployment_log_buffer_size,
        flush_interval=orch_settings.deployment_log_flush_interval,
        start_line_number=start_line,
    )


async def _get_nosana_signature(api_key: str) -> str:
    """
    Get the Nosana auth signature from the client-manager API.
    This signature is used for WebSocket authentication to Nosana nodes.

    Args:
        api_key: The Nosana API key from credentials

    Returns:
        The signature string
    """
    url = f"{NOSANA_CLIENT_MANAGER_URL}/auth/sign-message/external"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url,
                json={"message": "nosana-auth", "includeTime": False},
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
            ) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    raise Exception(f"Failed to get signature: {resp.status} - {text}")
                data = await resp.json()
                return data["signature"]
    except Exception as e:
        logger.error(f"Failed to get Nosana signature: {e}")
        raise


async def _get_nosana_api_key(credential_name: str | None) -> str:
    """
    Get the Nosana API key from credentials.

    Args:
        credential_name: The credential name (e.g., "default") or None

    Returns:
        The API key string

    Raises:
        Exception: If no API key is found
    """
    import asyncpg
    from cryptography.fernet import Fernet
    from inferia.services.orchestration.config import settings

    # Decrypt helper (copied from pool_repo.py)
    def _decrypt_value(value):
        if not value:
            return None
        encryption_key = settings.secret_encryption_key
        fernet = Fernet(encryption_key.encode()) if encryption_key else None

        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                return value
        if isinstance(value, dict) and "data" in value:
            if fernet:
                try:
                    decrypted = fernet.decrypt(value["data"].encode()).decode()
                    return json.loads(decrypted)
                except Exception:
                    pass
        return value

    # Get provider config directly from DB
    query = """
    SELECT value FROM system_settings WHERE key = 'providers_config' LIMIT 1
    """
    try:
        conn = await asyncpg.connect(POSTGRES_DSN)
        try:
            raw = await conn.fetchval(query)
            if not raw:
                raise Exception("No providers_config found in system_settings")

            # Decrypt if needed
            data = _decrypt_value(raw)

            if not data or not isinstance(data, dict):
                raise Exception("Invalid providers_config format")

            providers = data.get("providers", data)
            nosana_config = providers.get("depin", {}).get("nosana", {})

            # Try to find the API key from config
            # First check api_keys list
            api_keys = nosana_config.get("api_keys", [])
            for entry in api_keys:
                if entry.get("name") == (credential_name or "default"):
                    key = entry.get("key")
                    if key:
                        return key

            # Fall back to legacy single key (name "default")
            if not credential_name or credential_name == "default":
                key = nosana_config.get("api_key")
                if key:
                    return key

            raise Exception(
                f"No API key found for credential: {credential_name or 'default'}"
            )
        finally:
            await conn.close()
    except Exception as e:
        logger.error(f"Failed to get Nosana API key: {e}")
        raise Exception(f"Failed to get Nosana API key: {e}")


# ── gRPC client auth ────────────────────────────────────────────────
_INTERNAL_API_KEY = os.getenv("INTERNAL_API_KEY", "")

_GrpcClientCallDetails = collections.namedtuple(
    "_GrpcClientCallDetails",
    ("method", "timeout", "metadata", "credentials", "wait_for_ready"),
)


class _AuthInterceptor(grpc.aio.UnaryUnaryClientInterceptor):
    """Attaches x-internal-api-key metadata to all outgoing gRPC calls."""

    async def intercept_unary_unary(self, continuation, client_call_details, request):
        metadata = list(client_call_details.metadata or [])
        metadata.append(("x-internal-api-key", _INTERNAL_API_KEY))
        new_details = _GrpcClientCallDetails(
            method=client_call_details.method,
            timeout=client_call_details.timeout,
            metadata=metadata,
            credentials=client_call_details.credentials,
            wait_for_ready=client_call_details.wait_for_ready,
        )
        return await continuation(new_details, request)


def _auth_channel():
    """Create a gRPC channel that automatically attaches the internal API key."""
    return grpc.aio.insecure_channel(GRPC_ADDR, interceptors=[_AuthInterceptor()])


def _model_spec_from_request(req: "DeployModelRequest") -> dict:
    """Build the ``spec["model"]`` dict from a deploy request.

    Extracts artifact_uri / format / backend either from a nested
    req.configuration or falls back to the top-level engine / inference_model
    fields.

    Raises HTTPException(400) if no artifact_uri can be resolved.
    """
    cfg = req.configuration or {}
    model_block = cfg.get("model") or {}
    artifact_uri = (
        model_block.get("artifact_uri")
        or cfg.get("artifact_uri")
        or req.inference_model
        or req.model_name
    )
    if not artifact_uri:
        raise HTTPException(
            status_code=400,
            detail=(
                "model.artifact_uri (or inference_model / model_name) "
                "is required to load a model on a worker"
            ),
        )
    return {
        "artifact_uri": str(artifact_uri),
        "format": str(model_block.get("format") or cfg.get("format") or "hf"),
        "backend": str(
            model_block.get("backend")
            or cfg.get("backend")
            or req.engine
            or "vllm"
        ),
    }


router = APIRouter(prefix="/deployment", tags=["Deployment"])

POOL_STATE_RUNNING = "running"
POOL_STATE_TERMINATING = "terminating"
POOL_STATE_TERMINATED = "terminated"


async def _initiate_node_destroy(
    *,
    db_pool,
    jobs_repo,
    node_id,
    pool_id,
    org_id,
    provider: str,
) -> bool:
    """Mark the node terminating and enqueue a CancelJob.

    Returns True on success, False if the enqueue failed (in which case
    the metadata.terminating flag is still set so the dashboard can
    render the spinner — but the caller should signal the failure so
    the operator can retry).
    """
    async with db_pool.acquire() as conn:
        await conn.execute(
            "UPDATE compute_inventory SET metadata = "
            "jsonb_set(COALESCE(metadata,'{}'::jsonb), "
            "'{terminating}', 'true'::jsonb) WHERE id=$1",
            node_id,
        )
    try:
        await jobs_repo.enqueue(
            node_id=node_id,
            pool_id=pool_id,
            org_id=str(org_id) if org_id else "",
            provider=provider,
            spec={"cancel": True, "node_id": str(node_id)},
        )
        return True
    except Exception as e:
        logger.exception(
            "_initiate_node_destroy: enqueue failed for node=%s: %s",
            node_id, e,
        )
        return False


# status start stop


class DeployModelRequest(BaseModel):
    model_name: str
    model_version: str
    replicas: int
    gpu_per_replica: int
    workload_type: str = "inference"
    pool_id: str
    job_definition: dict | None = None

    # Unified fields
    engine: str | None = None
    configuration: dict | None = None
    owner_id: str | None = None
    endpoint: str | None = None
    org_id: str | None = None
    policies: dict | None = None
    inference_model: str | None = None
    model_type: str = "inference"  # inference, embedding, image_generation, etc.


class PreflightRequest(BaseModel):
    model_id: str
    hf_token: str | None = None
    engine: str | None = None
    gpu_per_replica: int = 1
    gpu_vram_gb: float = 24.0
    pool_id: str | None = None
    model_type: str = "inference"
    max_model_len: int | None = None
    image: str | None = None  # Docker image tag


class PreflightCheckResult(BaseModel):
    check: str
    passed: bool
    message: str | None = None
    needs_hf_token: bool = False


class PreflightResponse(BaseModel):
    ready: bool
    checks: list[PreflightCheckResult]


class TerminateDeploymentRequest(BaseModel):
    deployment_id: str


class UpdateDeploymentRequest(BaseModel):
    configuration: dict | None = None
    inference_model: str | None = None
    endpoint: str | None = None
    replicas: int | None = None


class CreatePoolRequest(BaseModel):
    pool_name: str
    owner_type: str
    owner_id: str
    provider: str
    allowed_gpu_types: list[str]
    max_cost_per_hour: float
    is_dedicated: bool
    provider_pool_id: str
    scheduling_policy_json: str
    provider_credential_name: str | None = (
        None  # Generic: which credential to use for this provider
    )
    gpu_count: int = 1  # Number of GPUs per node (for cluster provisioning)
    metadata: Optional[dict[str, Any]] = None  # Provider-specific pool config (e.g. AWS subnet_id)


class ModelRegistryRequest(BaseModel):
    model_name: str
    model_version: str
    backend: str
    artifact_uri: str
    config_json: dict


class DeleteModelRequest(BaseModel):
    model_id: str


# Audit Helper
def utcnow_naive():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).replace(tzinfo=None)


_AUDIT_CATEGORY_MAP = {
    "deployment.create": "deployment",
    "deployment.start": "deployment",
    "deployment.update": "deployment",
    "deployment.delete": "deployment",
    "deployment.terminate": "deployment",
    "pool.create": "deployment",
    "pool.stop": "deployment",
    "pool.delete": "deployment",
}


async def log_audit_event(
    user_id: str | None,
    action: str,
    resource_type: str,
    resource_id: str | None,
    details: dict | None = None,
    status: str = "success",
    org_id: str | None = None,
):
    import uuid

    category = _AUDIT_CATEGORY_MAP.get(action, action.split(".")[0] if "." in action else action)

    conn = None
    try:
        conn = await asyncpg.connect(POSTGRES_DSN)

        # Proactive check for user_id existence to avoid FK violation
        if user_id:
            exists = await conn.fetchval(
                "SELECT EXISTS(SELECT 1 FROM users WHERE id = $1)", user_id
            )
            if not exists:
                user_id = None

        await conn.execute(
            """
            INSERT INTO audit_logs (id, timestamp, user_id, action, resource_type, resource_id, details, status, org_id, category)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
         """,
            str(uuid.uuid4()),
            utcnow_naive(),
            user_id,
            action,
            resource_type,
            resource_id,
            json.dumps(details) if details else None,
            status,
            org_id,
            category,
        )
    except Exception as e:
        logger.error(f"Failed to write audit log: {e}")
    finally:
        if conn:
            await conn.close()


async def _lookup_org_id(resource_type: str, resource_id: str) -> str | None:
    """Look up org_id from a deployment or pool record for audit logging."""
    conn = None
    try:
        conn = await asyncpg.connect(POSTGRES_DSN)
        if resource_type == "deployment":
            return await conn.fetchval(
                "SELECT org_id FROM model_deployments WHERE deployment_id = $1",
                UUID(resource_id),
            )
        elif resource_type == "compute_pool":
            row = await conn.fetchrow(
                "SELECT owner_type, owner_id FROM compute_pools WHERE id = $1",
                UUID(resource_id),
            )
            if row and row["owner_type"] == "org":
                return row["owner_id"]
    except Exception as e:
        logger.warning(f"Failed to look up org_id for {resource_type}/{resource_id}: {e}")
    finally:
        if conn:
            await conn.close()
    return None


async def _get_pool_lifecycle_state(pool_id: UUID) -> str | None:
    conn = None
    try:
        conn = await asyncpg.connect(POSTGRES_DSN)
        lifecycle_state = await conn.fetchval(
            """
            SELECT lifecycle_state::text
            FROM compute_pools
            WHERE id = $1 AND is_active = TRUE
            """,
            pool_id,
        )
        if not lifecycle_state:
            return None
        return lifecycle_state
    finally:
        if conn:
            await conn.close()


async def _terminate_pool_background(pool_id: UUID):
    conn = None
    try:
        conn = await asyncpg.connect(POSTGRES_DSN)
        pool = await conn.fetchrow(
            """
            SELECT id, pool_name, provider, pool_type, cluster_id, provider_credential_name, lifecycle_state, is_active
            FROM compute_pools
            WHERE id = $1
            """,
            pool_id,
        )
        if not pool or not pool["is_active"]:
            return

        lifecycle_state = (pool["lifecycle_state"] or POOL_STATE_RUNNING).lower()
        if lifecycle_state != POOL_STATE_TERMINATING:
            return

        # Cluster pools require provider teardown; job pools can transition immediately.
        if pool["pool_type"] == "cluster" and pool["cluster_id"]:
            adapter = get_adapter(pool["provider"])
            capabilities = adapter.get_capabilities()
            if capabilities and capabilities.supports_cluster_mode:
                logger.info(
                    f"Stopping pool '{pool['pool_name']}' by terminating cluster '{pool['cluster_id']}'"
                )
                await adapter.terminate_cluster(
                    cluster_id=pool["cluster_id"],
                    provider_credential_name=pool["provider_credential_name"],
                )

        await conn.execute(
            """
            UPDATE compute_pools
            SET lifecycle_state = $2,
                updated_at = now()
            WHERE id = $1 AND is_active = TRUE
            """,
            pool_id,
            POOL_STATE_TERMINATED,
        )
        logger.info(f"Pool {pool_id} transitioned to terminated")
    except Exception as e:
        logger.exception(f"Failed to stop pool {pool_id}: {e}")
        if conn:
            await conn.execute(
                """
                UPDATE compute_pools
                SET lifecycle_state = $2,
                    updated_at = now()
                WHERE id = $1 AND is_active = TRUE
                """,
                pool_id,
                POOL_STATE_RUNNING,
            )
    finally:
        if conn:
            await conn.close()


@router.post("/preflight")
async def deployment_preflight(req: PreflightRequest):
    """Run pre-deployment checks before creating a deployment."""
    from inferia.services.orchestration.services.model_deployment.preflight import (
        check_model_accessibility,
        check_model_format,
        check_ollama_model_exists,
        fetch_hf_model_info,
        check_vram_fit,
        check_pipeline_compatibility,
        check_docker_image_exists,
        check_duplicate_deployment,
        check_context_length,
        ENGINES_WITH_OWN_REGISTRY,
    )

    checks = []

    # Skip HF checks for external engines
    external_engines = {"openai", "anthropic", "gemini", "groq", "cerebras", "mistral", "deepseek", "custom"}
    is_external = req.engine and req.engine.lower() in external_engines

    # Engines with their own model registry (ollama, localai) use model:tag format,
    # not HuggingFace org/model — skip all HF-based checks for these
    uses_own_registry = req.engine and req.engine.lower() in ENGINES_WITH_OWN_REGISTRY

    hf_info = None  # Shared metadata for multiple checks

    if not is_external and not uses_own_registry and req.model_id:
        # Check 1: Model accessibility
        result = await check_model_accessibility(req.model_id, req.hf_token)
        if result.skipped:
            checks.append(PreflightCheckResult(
                check="model_accessible",
                passed=True,
                message="HuggingFace API unreachable — check skipped.",
            ))
        elif result.accessible:
            checks.append(PreflightCheckResult(
                check="model_accessible",
                passed=True,
                message="Model is accessible.",
            ))
        else:
            checks.append(PreflightCheckResult(
                check="model_accessible",
                passed=False,
                message=result.error,
                needs_hf_token=result.needs_token,
            ))

        # Fetch full HF metadata once (used by checks 2-5)
        if result.accessible and not result.skipped:
            hf_info = await fetch_hf_model_info(req.model_id, req.hf_token)

        # Check 2: Model format compatibility
        if hf_info and req.engine:
            fmt = await check_model_format(req.model_id, req.engine, req.hf_token)
            if fmt.skipped:
                checks.append(PreflightCheckResult(
                    check="model_format",
                    passed=True,
                    message=f"Format check not required for {req.engine}.",
                ))
            elif fmt.compatible:
                checks.append(PreflightCheckResult(
                    check="model_format",
                    passed=True,
                    message="Model format is compatible.",
                ))
            else:
                checks.append(PreflightCheckResult(
                    check="model_format",
                    passed=False,
                    message=fmt.error,
                ))

        # Check 3: VRAM estimation
        if hf_info:
            vram = check_vram_fit(hf_info, req.gpu_per_replica, req.gpu_vram_gb)
            if vram.skipped:
                checks.append(PreflightCheckResult(
                    check="vram_estimate",
                    passed=True,
                    message="VRAM check skipped — parameter count unavailable.",
                ))
            elif vram.ok:
                checks.append(PreflightCheckResult(
                    check="vram_estimate",
                    passed=True,
                    message=f"Model fits: ~{vram.estimated_vram_gb} GB needed, {vram.available_vram_gb} GB available.",
                ))
            else:
                checks.append(PreflightCheckResult(
                    check="vram_estimate",
                    passed=False,
                    message=vram.error,
                ))

        # Check 4: Pipeline tag vs engine compatibility
        if hf_info and req.engine:
            pipe = check_pipeline_compatibility(hf_info, req.engine, req.model_type)
            if pipe.skipped:
                checks.append(PreflightCheckResult(
                    check="pipeline_compatible",
                    passed=True,
                    message="Pipeline compatibility check skipped.",
                ))
            elif pipe.compatible:
                checks.append(PreflightCheckResult(
                    check="pipeline_compatible",
                    passed=True,
                    message=f"Model pipeline '{pipe.pipeline_tag}' is compatible with {req.engine}.",
                ))
            else:
                checks.append(PreflightCheckResult(
                    check="pipeline_compatible",
                    passed=False,
                    message=pipe.error,
                ))

        # Check 5: Context length
        if hf_info and req.max_model_len:
            ctx = check_context_length(hf_info, req.max_model_len)
            if not ctx.skipped:
                checks.append(PreflightCheckResult(
                    check="context_length",
                    passed=ctx.ok,
                    message=ctx.error if not ctx.ok else "Requested context length is within model limits.",
                ))
    else:
        if uses_own_registry:
            # Validate against the engine's own registry
            if req.engine and req.engine.lower() == "ollama" and req.model_id:
                ollama_result = await check_ollama_model_exists(req.model_id)
                if ollama_result.skipped:
                    checks.append(PreflightCheckResult(
                        check="model_accessible",
                        passed=True,
                        message="Ollama registry unreachable — check skipped.",
                    ))
                elif ollama_result.accessible:
                    checks.append(PreflightCheckResult(
                        check="model_accessible",
                        passed=True,
                        message=f"Model '{req.model_id}' found in Ollama registry.",
                    ))
                else:
                    checks.append(PreflightCheckResult(
                        check="model_accessible",
                        passed=False,
                        message=ollama_result.error,
                    ))
            else:
                checks.append(PreflightCheckResult(
                    check="model_accessible",
                    passed=True,
                    message=f"{req.engine} uses its own model registry — check skipped.",
                ))
        else:
            checks.append(PreflightCheckResult(
                check="model_accessible",
                passed=True,
                message="External provider — HuggingFace checks not applicable.",
            ))

    # Check 6: Docker image existence (applies to all managed deployments)
    if req.image and not is_external:
        img = await check_docker_image_exists(req.image)
        if img.skipped:
            checks.append(PreflightCheckResult(
                check="docker_image",
                passed=True,
                message="Docker image check skipped.",
            ))
        elif img.exists:
            checks.append(PreflightCheckResult(
                check="docker_image",
                passed=True,
                message="Docker image exists.",
            ))
        else:
            checks.append(PreflightCheckResult(
                check="docker_image",
                passed=False,
                message=img.error,
            ))

    # Check 7: Duplicate deployment on same pool
    if req.model_id and req.pool_id and not is_external:
        try:
            db_pool = await asyncpg.create_pool(POSTGRES_DSN, min_size=1, max_size=1)
            dup = await check_duplicate_deployment(req.model_id, req.pool_id, db_pool)
            await db_pool.close()
            if not dup.skipped:
                checks.append(PreflightCheckResult(
                    check="duplicate_deployment",
                    passed=dup.ok,
                    message=dup.error if not dup.ok else "No duplicate deployment found.",
                ))
        except Exception as e:
            logger.warning("Duplicate check DB connection failed: %s", e)

    ready = all(c.passed for c in checks)
    return PreflightResponse(ready=ready, checks=checks)


@router.post("/deploy")
async def deploy_model(req: DeployModelRequest, request: Request):
    """Pool-first deploy.

    1. Validate pool exists and is running.
    2. Create deployment row in CREATED state.
    3. Inside a transaction:
       - PoolPlacer.place(tx=conn) decides where the model goes.
       - BindToReady: allocate GPU, bind, set DEPLOYING. Commit. Then load_model.
       - CoWaitOnProvisioning: allocate, bind, set PENDING_NODE.
       - ColdStart: create_placeholder, bind, set PENDING_NODE. For
         worker-pools, return. Otherwise enqueue ProvisioningJob.
    4. PoolAtCapacity is caught and surfaced as 503.
    """
    from uuid import UUID, uuid4
    from fastapi.responses import JSONResponse
    from inferia.services.orchestration.services.model_deployment.pool_placer import (
        PoolPlacer, BindToReady, CoWaitOnProvisioning, ColdStart, PoolAtCapacity,
    )
    from inferia.services.orchestration.repositories.inventory_repo import (
        InventoryRepository,
    )
    from inferia.services.orchestration.repositories.model_deployment_repo import (
        ModelDeploymentRepository,
    )
    from inferia.services.orchestration.repositories.pool_repo import (
        ComputePoolRepository,
    )
    from inferia.services.orchestration.services.provisioning.jobs.repository import (
        ProvisioningJobRepository,
    )

    db_pool = request.app.state.pool
    controller = request.app.state.worker_controller
    event_bus = getattr(request.app.state, "event_bus", None)

    pool_repo = ComputePoolRepository(db_pool)
    inventory = InventoryRepository(db_pool)
    deploys = ModelDeploymentRepository(db_pool, event_bus=event_bus)
    placer = PoolPlacer(db_pool)
    jobs_repo = ProvisioningJobRepository(db_pool)

    # 1. Validate pool
    try:
        pool_id_uuid = UUID(req.pool_id)
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail="pool_id must be a UUID")
    pool_row = await pool_repo.get(pool_id_uuid)
    if pool_row is None:
        raise HTTPException(status_code=404, detail="pool not found")
    lifecycle = (pool_row.get("lifecycle_state") or "running").lower()
    if lifecycle in ("terminating", "terminated"):
        raise HTTPException(status_code=409,
                             detail=f"pool is {lifecycle}")

    # Normalize the pool's metadata — asyncpg returns jsonb columns as str
    _raw_meta = pool_row.get("metadata")
    if isinstance(_raw_meta, str):
        try:
            pool_meta: dict = json.loads(_raw_meta)
        except (ValueError, TypeError):
            pool_meta = {}
    elif isinstance(_raw_meta, dict):
        pool_meta = _raw_meta
    else:
        pool_meta = {}
    if not isinstance(pool_meta, dict):
        pool_meta = {}

    # Duplicate-name guard: 409 if any non-terminal deploy with this
    # model_name already exists in the same org.
    if req.model_name and req.org_id:
        async with db_pool.acquire() as _c:
            dup_row = await _c.fetchrow(
                """
                SELECT deployment_id FROM model_deployments
                 WHERE model_name = $1
                   AND org_id = $2
                   AND state NOT IN ('STOPPED', 'TERMINATED', 'FAILED')
                """,
                req.model_name, str(req.org_id),
            )
        if dup_row is not None:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"deployment '{req.model_name}' already exists "
                    f"in this org; stop it first"
                ),
            )

    # 2. Create deployment row (CREATED state, target_pool_id=pool_id)
    deploy_id = uuid4()
    policies_val = req.policies
    if policies_val is not None and isinstance(policies_val, dict):
        policies_val = json.dumps(policies_val)
    configuration_val = req.configuration
    if configuration_val is not None and isinstance(configuration_val, dict):
        configuration_val = json.dumps(configuration_val)
    await deploys.create(
        deployment_id=deploy_id,
        model_id=None,
        pool_id=pool_id_uuid,
        replicas=req.replicas,
        gpu_per_replica=req.gpu_per_replica,
        state="CREATED",
        engine=req.engine,
        configuration=configuration_val,
        endpoint=req.endpoint,
        model_name=req.model_name,
        owner_id=req.owner_id,
        org_id=req.org_id,
        policies=policies_val,
        inference_model=req.inference_model,
        model_type=req.model_type,
        target_pool_id=pool_id_uuid,
        target_node_id=None,
    )

    # 3. Transactional decision + bind
    # pending_enqueue: set after tx commits; contains kwargs for jobs_repo.enqueue
    bound_for_load: tuple | None = None  # (node_id, deploy_id) for warm path
    pending_enqueue: dict | None = None  # post-tx provisioning job to enqueue
    response_body: dict = {}
    response_status = 200
    try:
        async with db_pool.acquire() as conn:
            async with conn.transaction():
                decision = await placer.place(
                    pool_id=pool_id_uuid,
                    gpu_required=req.gpu_per_replica or 0,
                    tx=conn,
                )

                if isinstance(decision, BindToReady):
                    ok = await inventory.allocate_gpu(
                        decision.node_id, req.gpu_per_replica or 0, tx=conn,
                    )
                    if not ok:
                        # capacity raced; one retry inside the same tx
                        decision = await placer.place(
                            pool_id=pool_id_uuid,
                            gpu_required=req.gpu_per_replica or 0,
                            tx=conn,
                        )
                        if isinstance(decision, BindToReady):
                            ok = await inventory.allocate_gpu(
                                decision.node_id, req.gpu_per_replica or 0,
                                tx=conn,
                            )
                            if not ok:
                                raise HTTPException(
                                    status_code=503,
                                    detail="capacity race lost twice; retry",
                                )
                            await deploys.bind_to_node(
                                deploy_id, decision.node_id, tx=conn,
                            )
                            await deploys.set_state(
                                deploy_id, "DEPLOYING", tx=conn,
                            )
                            bound_for_load = (decision.node_id, deploy_id)
                            response_body = {
                                "deployment_id": str(deploy_id),
                                "state": "DEPLOYING",
                                "target_node_id": str(decision.node_id),
                            }
                            response_status = 200
                        elif isinstance(decision, CoWaitOnProvisioning):
                            await inventory.allocate_gpu(
                                decision.node_id, req.gpu_per_replica or 0,
                                tx=conn,
                            )
                            await deploys.bind_to_node(
                                deploy_id, decision.node_id, tx=conn,
                            )
                            await deploys.set_state(
                                deploy_id, "PENDING_NODE", tx=conn,
                            )
                            response_body = {
                                "deployment_id": str(deploy_id),
                                "state": "PENDING_NODE",
                                "target_node_id": str(decision.node_id),
                                "message": "co-waiting on existing provisioning",
                            }
                            response_status = 202
                        else:
                            # ColdStart on retry
                            assert isinstance(decision, ColdStart)
                            node_id = await inventory.create_placeholder(
                                pool_id=pool_id_uuid,
                                gpu_total=decision.gpu_total_per_node,
                                initial_alloc=req.gpu_per_replica or 0,
                                tx=conn,
                            )
                            await deploys.bind_to_node(deploy_id, node_id, tx=conn)
                            await deploys.set_state(deploy_id, "PENDING_NODE", tx=conn)
                            is_worker_pool = pool_meta.get("agent_kind") == "worker"
                            response_body = {
                                "deployment_id": str(deploy_id),
                                "state": "PENDING_NODE",
                                "target_node_id": str(node_id),
                                "message": "waiting for worker registration"
                                if is_worker_pool else "provisioning compute",
                            }
                            response_status = 202
                            if not is_worker_pool:
                                # Collect for post-tx enqueue (FK needs committed node)
                                pending_enqueue = {
                                    "node_id": node_id,
                                    "pool_id": pool_id_uuid,
                                    "org_id": str(req.org_id) if req.org_id else "",
                                    "provider": decision.provider,
                                    "spec": {
                                        "pool_id": str(pool_id_uuid),
                                        "org_id": str(req.org_id) if req.org_id else None,
                                        "instance_type": pool_meta.get("instance_type"),
                                        "region": pool_meta.get("region"),
                                        "gpu_count": decision.gpu_total_per_node,
                                    },
                                }
                    else:
                        # first allocate_gpu succeeded — BindToReady warm path
                        await deploys.bind_to_node(deploy_id, decision.node_id, tx=conn)
                        await deploys.set_state(deploy_id, "DEPLOYING", tx=conn)
                        bound_for_load = (decision.node_id, deploy_id)
                        response_body = {
                            "deployment_id": str(deploy_id),
                            "state": "DEPLOYING",
                            "target_node_id": str(decision.node_id),
                        }
                        response_status = 200

                elif isinstance(decision, CoWaitOnProvisioning):
                    await inventory.allocate_gpu(
                        decision.node_id, req.gpu_per_replica or 0, tx=conn,
                    )
                    await deploys.bind_to_node(deploy_id, decision.node_id, tx=conn)
                    await deploys.set_state(deploy_id, "PENDING_NODE", tx=conn)
                    response_body = {
                        "deployment_id": str(deploy_id),
                        "state": "PENDING_NODE",
                        "target_node_id": str(decision.node_id),
                        "message": "co-waiting on existing provisioning",
                    }
                    response_status = 202

                else:
                    assert isinstance(decision, ColdStart)
                    node_id = await inventory.create_placeholder(
                        pool_id=pool_id_uuid,
                        gpu_total=decision.gpu_total_per_node,
                        initial_alloc=req.gpu_per_replica or 0,
                        tx=conn,
                    )
                    await deploys.bind_to_node(deploy_id, node_id, tx=conn)
                    await deploys.set_state(deploy_id, "PENDING_NODE", tx=conn)
                    is_worker_pool = pool_meta.get("agent_kind") == "worker"
                    response_body = {
                        "deployment_id": str(deploy_id),
                        "state": "PENDING_NODE",
                        "target_node_id": str(node_id),
                        "message": "waiting for worker registration"
                        if is_worker_pool else "provisioning compute",
                    }
                    response_status = 202
                    if not is_worker_pool:
                        # Collect for post-tx enqueue (FK needs committed node)
                        pending_enqueue = {
                            "node_id": node_id,
                            "pool_id": pool_id_uuid,
                            "org_id": str(req.org_id) if req.org_id else "",
                            "provider": decision.provider,
                            "spec": {
                                "pool_id": str(pool_id_uuid),
                                "org_id": str(req.org_id) if req.org_id else None,
                                "instance_type": pool_meta.get("instance_type"),
                                "region": pool_meta.get("region"),
                                "gpu_count": decision.gpu_total_per_node,
                            },
                        }

    except PoolAtCapacity as e:
        await deploys.set_state(deploy_id, "FAILED")
        from fastapi.responses import JSONResponse
        return JSONResponse(
            status_code=503,
            content={
                "error": "POOL_AT_CAPACITY",
                "current_nodes": e.current_nodes,
                "max_nodes": e.max_nodes,
                "deployment_id": str(deploy_id),
            },
            headers={"Retry-After": "60"},
        )
    except HTTPException:
        # Already a structured client error (e.g. capacity race lost twice);
        # mark FAILED and re-raise.
        await deploys.set_state(deploy_id, "FAILED")
        raise
    except Exception as e:
        logger.exception("deploy: unexpected error for %s: %s", deploy_id, e)
        await deploys.set_state(deploy_id, "FAILED")
        raise HTTPException(
            status_code=500,
            detail=f"deploy failed: {e.__class__.__name__}",
        )

    # 4a. Post-tx: enqueue provisioning job (requires committed placeholder node for FK)
    if pending_enqueue is not None:
        try:
            await jobs_repo.enqueue(**pending_enqueue)
        except Exception as e:
            logger.exception("deploy: enqueue failed for %s: %s", deploy_id, e)
            # Rollback: mark FAILED + release the placeholder GPU.
            async with db_pool.acquire() as _c:
                async with _c.transaction():
                    await inventory.release_gpu(
                        pending_enqueue["node_id"], req.gpu_per_replica or 0,
                        tx=_c,
                    )
                    await deploys.set_state(deploy_id, "FAILED", tx=_c)
            raise HTTPException(
                status_code=502,
                detail=f"failed to enqueue provisioning job: {e}",
            )

    # 4b. Post-tx: load_model for the warm path
    if bound_for_load is not None:
        node_id, _ = bound_for_load
        try:
            spec = {
                "deployment_id": str(deploy_id),
                "recipe": (req.engine or "vllm"),
                "model": _model_spec_from_request(req),
                "config": (req.configuration or {}).get("config") or {},
                "gpu_indices": list(range(req.gpu_per_replica or 0)),
                "port": 0,
            }
            await controller.load_model(node_id=str(node_id), spec=spec)
        except Exception as exc:
            logger.exception("deploy: load_model failed for %s: %s", deploy_id, exc)
            # Rollback: release the GPU and mark FAILED
            async with db_pool.acquire() as conn:
                async with conn.transaction():
                    await inventory.release_gpu(
                        node_id, req.gpu_per_replica or 0, tx=conn,
                    )
                    await deploys.set_state(deploy_id, "FAILED", tx=conn)
            raise HTTPException(status_code=502,
                                  detail=f"load_model failed: {exc}")

    await log_audit_event(
        user_id=req.owner_id,
        action="deployment.create",
        resource_type="deployment",
        resource_id=str(deploy_id),
        details={
            "model_name": req.model_name,
            "pool_id": str(pool_id_uuid),
            "final_state": response_body.get("state"),
        },
        status="success",
        org_id=str(req.org_id) if req.org_id else None,
    )

    from fastapi.responses import JSONResponse
    return JSONResponse(status_code=response_status, content=response_body)


@router.get("/status/{deployment_id}")
async def get_deployment_status(deployment_id: str):
    async with _auth_channel() as channel:
        stub = model_deployment_pb2_grpc.ModelDeploymentServiceStub(channel)

        try:
            resp = await stub.GetDeployment(
                model_deployment_pb2.GetDeploymentRequest(deployment_id=deployment_id)
            )
        except grpc.RpcError as e:
            raise HTTPException(
                status_code=404,
                detail=f"Deployment not found: {e.details()}",
            )

    return {
        "deployment_id": resp.deployment_id,
        "state": resp.state,
        "replicas": resp.replicas,
        "pool_id": resp.pool_id,
        "model_name": resp.model_name,
        "model_version": resp.model_version,
        "configuration": json.loads(resp.configuration) if resp.configuration else {},
        "owner_id": resp.owner_id,
        "endpoint": resp.endpoint,
        "org_id": resp.org_id,
        "policies": json.loads(resp.policies) if resp.policies else {},
        "engine": resp.engine,
        "inference_model": resp.inference_model,
        "error_message": resp.error_message or None,
    }


@router.patch("/update/{deployment_id}")
async def update_deployment(deployment_id: str, req: UpdateDeploymentRequest):
    async with _auth_channel() as channel:
        stub = model_deployment_pb2_grpc.ModelDeploymentServiceStub(channel)

        try:
            update_kwargs = {"deployment_id": deployment_id}
            if req.configuration is not None:
                update_kwargs["configuration"] = json.dumps(req.configuration)
            if req.inference_model is not None:
                update_kwargs["inference_model"] = req.inference_model
            if req.endpoint is not None:
                update_kwargs["endpoint"] = req.endpoint
            if req.replicas is not None:
                update_kwargs["replicas"] = req.replicas

            resp = await stub.UpdateDeployment(
                model_deployment_pb2.UpdateDeploymentRequest(**update_kwargs)
            )

            # Log Audit Event
            await log_audit_event(
                user_id=None,
                action="deployment.update",
                resource_type="deployment",
                resource_id=deployment_id,
                details=req.model_dump(exclude_none=True),
                org_id=await _lookup_org_id("deployment", deployment_id),
            )

            return {"success": resp.success, "message": resp.message}

        except grpc.RpcError as e:
            raise HTTPException(
                status_code=500,
                detail=f"Update failed: {e.details()}",
            )


@router.post("/terminate")
async def terminate_deployment(req: TerminateDeploymentRequest, request: Request):
    """Refcount-aware deploy termination.

    PENDING_NODE: unbind + release_gpu; if refcount=0 -> destroy node.
    DEPLOYING/RUNNING: unload_model + release_gpu; if refcount=0 -> destroy.
    Terminal states: no-op.
    """
    from uuid import UUID
    from inferia.services.orchestration.repositories.inventory_repo import (
        InventoryRepository,
    )
    from inferia.services.orchestration.repositories.model_deployment_repo import (
        ModelDeploymentRepository,
    )
    from inferia.services.orchestration.repositories.pool_repo import (
        ComputePoolRepository,
    )
    from inferia.services.orchestration.services.provisioning.jobs.repository import (
        ProvisioningJobRepository,
    )

    db_pool = request.app.state.pool
    controller = request.app.state.worker_controller
    event_bus = getattr(request.app.state, "event_bus", None)

    inventory = InventoryRepository(db_pool)
    deploys = ModelDeploymentRepository(db_pool, event_bus=event_bus)
    pool_repo = ComputePoolRepository(db_pool)
    jobs_repo = ProvisioningJobRepository(db_pool)

    try:
        deploy_uuid = UUID(req.deployment_id)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="invalid deployment_id")

    row = await deploys.get(deploy_uuid)
    if row is None:
        raise HTTPException(status_code=404, detail="deployment not found")

    state = row.get("state")
    target_node_id = row.get("target_node_id")
    gpu_per_replica = int(row.get("gpu_per_replica") or 0)
    pool_id = row.get("pool_id") or row.get("target_pool_id")
    org_id = row.get("org_id")

    if state in ("STOPPED", "TERMINATED", "FAILED"):
        await log_audit_event(
            user_id=None,
            action="deployment.terminate",
            resource_type="deployment",
            resource_id=str(deploy_uuid),
            status="success",
            org_id=org_id,
            details={"already_terminal": state},
        )
        return {"deployment_id": str(deploy_uuid), "status": state}

    # Determine pool provider for the potential destroy enqueue.
    pool_provider = None
    if pool_id is not None:
        pool_row = await pool_repo.get(pool_id)
        if pool_row is not None:
            pool_provider = pool_row.get("provider")

    # Fallback: read provider directly from compute_inventory when the
    # pool row is gone (soft-deleted between deploy creation and now).
    if pool_provider is None and target_node_id is not None:
        async with db_pool.acquire() as _c:
            pool_provider = await _c.fetchval(
                "SELECT provider::text FROM compute_inventory WHERE id=$1",
                target_node_id,
            )

    if state == "PENDING_NODE":
        async with db_pool.acquire() as conn:
            async with conn.transaction():
                await deploys.unbind(deploy_uuid, tx=conn)
                await deploys.set_state(deploy_uuid, "TERMINATED", tx=conn)
                should_destroy = False
                if target_node_id is not None and gpu_per_replica > 0:
                    result = await inventory.release_gpu(
                        target_node_id, gpu_per_replica, tx=conn,
                    )
                    should_destroy = result.should_destroy
        if should_destroy and target_node_id is not None and pool_provider:
            destroy_ok = await _initiate_node_destroy(
                db_pool=db_pool, jobs_repo=jobs_repo,
                node_id=target_node_id, pool_id=pool_id,
                org_id=org_id, provider=pool_provider,
            )
            if not destroy_ok:
                # The deploy is TERMINATED in the DB; the node is flagged
                # terminating; but the destroy job didn't enqueue. Signal
                # so the operator retries (consistent with T7's /deploy).
                await log_audit_event(
                    user_id=None,
                    action="deployment.terminate",
                    resource_type="deployment",
                    resource_id=str(deploy_uuid),
                    status="partial",
                    org_id=org_id,
                    details={"prev_state": "PENDING_NODE",
                              "destroy_enqueued": False},
                )
                raise HTTPException(
                    status_code=502,
                    detail=(
                        f"deploy terminated but destroy job enqueue failed "
                        f"for node {target_node_id}; retry to clean up"
                    ),
                )
        await log_audit_event(
            user_id=None,
            action="deployment.terminate",
            resource_type="deployment",
            resource_id=str(deploy_uuid),
            status="success",
            org_id=org_id,
            details={"prev_state": "PENDING_NODE",
                      "destroyed_node": str(target_node_id) if should_destroy else None},
        )
        return {"deployment_id": str(deploy_uuid), "status": "TERMINATED"}

    # DEPLOYING / RUNNING / CREATED
    if target_node_id is not None:
        try:
            await controller.unload_model(
                node_id=str(target_node_id),
                deployment_id=str(deploy_uuid),
            )
        except Exception as e:
            logger.warning(
                "terminate: unload_model failed for %s on %s: %s",
                deploy_uuid, target_node_id, e,
            )

    async with db_pool.acquire() as conn:
        async with conn.transaction():
            await deploys.update_state(
                deployment_id=deploy_uuid,
                state="TERMINATED",
                tx=conn,
            )
            should_destroy = False
            if target_node_id is not None and gpu_per_replica > 0:
                result = await inventory.release_gpu(
                    target_node_id, gpu_per_replica, tx=conn,
                )
                should_destroy = result.should_destroy

    if should_destroy and target_node_id is not None and pool_provider:
        destroy_ok = await _initiate_node_destroy(
            db_pool=db_pool, jobs_repo=jobs_repo,
            node_id=target_node_id, pool_id=pool_id,
            org_id=org_id, provider=pool_provider,
        )
        if not destroy_ok:
            # The deploy is TERMINATED in the DB; the node is flagged
            # terminating; but the destroy job didn't enqueue. Signal
            # so the operator retries (consistent with T7's /deploy).
            await log_audit_event(
                user_id=None,
                action="deployment.terminate",
                resource_type="deployment",
                resource_id=str(deploy_uuid),
                status="partial",
                org_id=org_id,
                details={"prev_state": state,
                          "destroy_enqueued": False},
            )
            raise HTTPException(
                status_code=502,
                detail=(
                    f"deploy terminated but destroy job enqueue failed "
                    f"for node {target_node_id}; retry to clean up"
                ),
            )

    await log_audit_event(
        user_id=None,
        action="deployment.terminate",
        resource_type="deployment",
        resource_id=str(deploy_uuid),
        status="success",
        org_id=org_id,
        details={"prev_state": state,
                  "destroyed_node": str(target_node_id) if should_destroy else None},
    )
    return {"deployment_id": str(deploy_uuid), "status": "TERMINATED"}


@router.post("/start")
async def start_deployment(
    req: TerminateDeploymentRequest,
):  # Reusing same request body structure
    async with _auth_channel() as channel:
        stub = model_deployment_pb2_grpc.ModelDeploymentServiceStub(channel)

        try:
            resp = await stub.StartDeployment(
                model_deployment_pb2.StartDeploymentRequest(
                    deployment_id=req.deployment_id
                )
            )
            await log_audit_event(
                user_id=None,
                action="deployment.start",
                resource_type="deployment",
                resource_id=req.deployment_id,
                status="success",
                org_id=await _lookup_org_id("deployment", req.deployment_id),
            )
        except grpc.RpcError as e:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to start deployment: {e.details()}",
            )

    return {
        "deployment_id": req.deployment_id,
        "status": resp.state,
    }


@router.delete("/delete/{deployment_id}")
async def delete_deployment(deployment_id: str):
    """Permanently delete a deployment from the database.

    This should only be called on deployments that are already STOPPED or TERMINATED.
    For running deployments, use /terminate first.
    """
    import asyncpg
    from uuid import UUID

    try:
        # Validate UUID format
        dep_uuid = UUID(deployment_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid deployment ID format")

    # Capture org_id BEFORE deletion so the audit log has it
    audit_org_id = await _lookup_org_id("deployment", deployment_id)

    try:
        conn = await asyncpg.connect(POSTGRES_DSN)
        try:
            # Check if deployment exists and is stopped
            row = await conn.fetchrow(
                "SELECT state FROM model_deployments WHERE deployment_id = $1", dep_uuid
            )

            if not row:
                raise HTTPException(status_code=404, detail="Deployment not found")

            # Only allow deletion of stopped/terminated/failed deployments
            if row["state"] not in ("STOPPED", "TERMINATED", "FAILED"):
                raise HTTPException(
                    status_code=400,
                    detail=f"Cannot delete deployment in state '{row['state']}'. Stop it first.",
                )

            # Clean up dependent records explicitly so deletion works even when
            # DB constraints were created without ON DELETE behaviors.
            async with conn.transaction():
                await conn.execute(
                    "UPDATE policies SET deployment_id = NULL WHERE deployment_id = $1",
                    dep_uuid,
                )
                await conn.execute(
                    "UPDATE api_keys SET deployment_id = NULL WHERE deployment_id = $1",
                    dep_uuid,
                )
                await conn.execute(
                    "DELETE FROM inference_logs WHERE deployment_id = $1",
                    dep_uuid,
                )
                await conn.execute(
                    "DELETE FROM model_deployments WHERE deployment_id = $1", dep_uuid
                )
        finally:
            await conn.close()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Failed to delete deployment: {str(e)}"
        )

    await log_audit_event(
        user_id=None,
        action="deployment.delete",
        resource_type="deployment",
        resource_id=deployment_id,
        status="success",
        org_id=audit_org_id,
    )

    return {
        "deployment_id": deployment_id,
        "status": "DELETED",
    }


@router.post("/createpool")
async def create_pool(req: CreatePoolRequest, request: Request):
    """Metadata-only pool creation.

    Inserts a `compute_pools` row and persists metadata/org_id. Does NOT
    spawn any compute_inventory rows; does NOT kick off Pulumi or any
    other provisioning. All provisioning happens at /deploy time via
    PoolPlacer when an actual model deploy arrives (T7).
    """
    from uuid import UUID, uuid4
    from inferia.services.orchestration.repositories.pool_repo import (
        ComputePoolRepository,
    )

    # 1. Validate provider
    try:
        get_adapter(req.provider)
    except ValueError as e:
        raise HTTPException(
            status_code=400, detail=f"Invalid provider '{req.provider}'. {str(e)}",
        )

    # 2. Validate AWS pool metadata
    if req.provider == "aws" and req.metadata is not None:
        from inferia.services.orchestration.services.adapter_engine.adapters.aws.pool_metadata import (
            AWSPoolMetadata,
        )
        from pydantic import ValidationError as _ValidationError
        try:
            AWSPoolMetadata(**req.metadata)
        except _ValidationError as e:
            raise HTTPException(status_code=422, detail={"errors": e.errors()})

    # 3. Multi-org header override (api_gateway forwards resolved org)
    hdr_org = request.headers.get("x-organization-id")
    if hdr_org and req.owner_type == "user":
        req.owner_id = hdr_org
        req.owner_type = "organization"

    db_pool = request.app.state.pool
    pool_repo = ComputePoolRepository(db_pool)

    # 4. Determine pool_type from adapter capabilities (preserves the
    #    legacy 'cluster' vs 'job' shape the gRPC servicer used to set).
    try:
        adapter = get_adapter(req.provider)
        caps = adapter.get_capabilities()
        pool_type = "cluster" if caps and caps.supports_cluster_mode else "job"
    except Exception:
        pool_type = "job"

    # 5. Build pool data and insert
    pool_id = uuid4()
    pool_data = {
        "id": pool_id,
        "pool_name": req.pool_name,
        "owner_type": req.owner_type,
        "owner_id": req.owner_id,
        "provider": req.provider,
        "pool_type": pool_type,
        "allowed_gpu_types": list(req.allowed_gpu_types),
        "max_cost_per_hour": req.max_cost_per_hour,
        "is_dedicated": req.is_dedicated,
        "provider_pool_id": req.provider_pool_id,
        "scheduling_policy": req.scheduling_policy_json or '{"strategy":"best_fit"}',
        "is_active": True,
        "lifecycle_state": "running",
        "gpu_count": req.gpu_count or 1,
    }
    if req.provider_credential_name:
        # validate credential exists if specified
        ok = await pool_repo.credential_exists(
            req.provider, req.provider_credential_name,
        )
        if not ok:
            raise HTTPException(
                status_code=412,
                detail=(f"Credential '{req.provider_credential_name}' not "
                        f"found or inactive for provider '{req.provider}'"),
            )
        pool_data["provider_credential_name"] = req.provider_credential_name

    try:
        created_pool_id = await pool_repo.create_pool(pool_data)
    except Exception as e:
        # Duplicate (pool_name, owner) -> 409; other DB errors -> 500
        msg = str(e)
        if "UniqueViolationError" in type(e).__name__ or "unique" in msg.lower():
            raise HTTPException(
                status_code=409,
                detail=f"Pool '{req.pool_name}' already exists for this owner",
            )
        raise HTTPException(status_code=500, detail=f"create_pool failed: {e}")

    final_pool_id = created_pool_id if created_pool_id else pool_id

    # 6. Audit event
    await log_audit_event(
        user_id=req.owner_id if req.owner_type == "user" else None,
        action="pool.create",
        resource_type="compute_pool",
        resource_id=str(final_pool_id),
        details={
            "name": req.pool_name,
            "provider": req.provider,
            "gpu_types": list(req.allowed_gpu_types),
        },
        org_id=req.owner_id if req.owner_type in ("org", "organization") else None,
    )

    # 7. Persist org_id + metadata into compute_pools (separate UPDATE so
    #    the pool create stays minimal). Non-fatal if it fails — the pool
    #    row exists either way.
    try:
        async with db_pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE compute_pools
                   SET org_id = $2,
                       metadata = COALESCE($3::jsonb, metadata),
                       updated_at = now()
                 WHERE id = $1
                """,
                final_pool_id,
                req.owner_id,
                json.dumps(req.metadata) if req.metadata else None,
            )
    except Exception as e:
        logger.warning("createpool: metadata/org_id update failed: %s", e)

    return {"pool_id": str(final_pool_id), "status": "CREATED"}


@router.get("/list/pool/{pool_id}/inventory")
async def list_pool_inventory(pool_id: str):
    async with _auth_channel() as channel:
        stub = compute_pool_pb2_grpc.ComputePoolManagerStub(channel)

        try:
            resp = await stub.ListPoolInventory(
                compute_pool_pb2.ListPoolInventoryRequest(pool_id=pool_id)
            )
        except grpc.RpcError as e:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to list pool inventory: {e.details()}",
            )

    return {
        "pool_id": pool_id,
        "nodes": [
            {
                "node_id": node.node_id,
                "provider": node.provider,
                "state": node.state,
                "gpu_total": node.gpu_total,
                "gpu_allocated": node.gpu_allocated,
                "vcpu_total": node.vcpu_total,
                "vcpu_allocated": node.vcpu_allocated,
                "expose_url": node.expose_url,
            }
            for node in resp.nodes
            # if not node.state.lower().startswith("terminat") # Allowing terminated nodes if they exist for debug
        ],
    }


@router.get("/listPools/{owner_id}")
async def list_pools(
    owner_id: str | None = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    async with _auth_channel() as channel:
        stub = compute_pool_pb2_grpc.ComputePoolManagerStub(channel)

        try:
            resp = await stub.ListPools(
                compute_pool_pb2.ListPoolsRequest(owner_id=owner_id or "")
            )
        except grpc.RpcError as e:
            raise HTTPException(status_code=500, detail=e.details())

    # Enrich with GPU Specs from DB
    enriched_pools = []
    conn = None
    try:
        conn = await asyncpg.connect(POSTGRES_DSN)
        # Fetch available GPU specs
        resources = await conn.fetch(
            "SELECT DISTINCT gpu_type, gpu_memory_gb FROM provider_resources WHERE gpu_type IS NOT NULL"
        )
        gpu_resource_map = {
            r["gpu_type"].upper(): r["gpu_memory_gb"] for r in resources
        }
        pool_states = await conn.fetch(
            """
            SELECT id::text AS pool_id, lifecycle_state::text AS lifecycle_state
            FROM compute_pools
            WHERE is_active = TRUE
            """
        )
        pool_state_map = {
            row["pool_id"]: row["lifecycle_state"] or POOL_STATE_RUNNING
            for row in pool_states
        }

        for p in resp.pools:
            pool_dict = {
                "pool_id": p.pool_id,
                "pool_name": p.pool_name,
                "provider": p.provider,
                "is_active": p.is_active,
                "owner_type": p.owner_type,
                "owner_id": p.owner_id,
                "allowed_gpu_types": list(p.allowed_gpu_types),
                "max_cost_per_hour": p.max_cost_per_hour,
                "is_dedicated": p.is_dedicated,
                "scheduling_policy_json": p.scheduling_policy_json,
                "provider_pool_id": p.provider_pool_id,
                "provider_credential_name": p.provider_credential_name,
                "cluster_id": p.cluster_id,
                "pool_type": p.pool_type,
                "gpu_count": p.gpu_count or 1,
                "lifecycle_state": pool_state_map.get(p.pool_id, POOL_STATE_RUNNING),
                "created_at": p.created_at,
                "updated_at": p.updated_at,
                "gpu_specs": [],
            }
            # Add VRAM info if we have it in our map
            for gt in p.allowed_gpu_types:
                vram = gpu_resource_map.get(gt.upper())
                if vram:
                    pool_dict["gpu_specs"].append({"gpu_type": gt, "vram": vram})

            enriched_pools.append(pool_dict)
    except Exception:
        # Fallback to non-enriched if DB fails
        return {
            "pools": [
                {
                    "pool_id": p.pool_id,
                    "pool_name": p.pool_name,
                    "provider": p.provider,
                    "is_active": p.is_active,
                    "owner_type": p.owner_type,
                    "owner_id": p.owner_id,
                    "allowed_gpu_types": list(p.allowed_gpu_types),
                    "max_cost_per_hour": p.max_cost_per_hour,
                    "is_dedicated": p.is_dedicated,
                    "scheduling_policy_json": p.scheduling_policy_json,
                    "provider_pool_id": p.provider_pool_id,
                    "provider_credential_name": p.provider_credential_name,
                    "cluster_id": p.cluster_id,
                    "pool_type": p.pool_type,
                    "gpu_count": p.gpu_count or 1,
                    "lifecycle_state": POOL_STATE_RUNNING,
                    "created_at": p.created_at,
                    "updated_at": p.updated_at,
                }
                for p in resp.pools
            ]
        }
    finally:
        if conn:
            await conn.close()

    # Apply server-side pagination
    paginated = enriched_pools[offset : offset + limit]
    return {"pools": paginated, "total": len(enriched_pools)}


@router.get("/pool/{pool_id}")
async def get_pool(pool_id: str):
    async with _auth_channel() as channel:
        stub = compute_pool_pb2_grpc.ComputePoolManagerStub(channel)

        try:
            p = await stub.GetPool(compute_pool_pb2.GetPoolRequest(pool_id=pool_id))
        except grpc.RpcError as e:
            if e.code() == grpc.StatusCode.NOT_FOUND:
                raise HTTPException(status_code=404, detail="Pool not found")
            raise HTTPException(status_code=500, detail=e.details())

    try:
        lifecycle_state = (
            await _get_pool_lifecycle_state(UUID(pool_id))
        ) or POOL_STATE_RUNNING
    except Exception:
        lifecycle_state = POOL_STATE_RUNNING

    return {
        "pool_id": p.pool_id,
        "pool_name": p.pool_name,
        "provider": p.provider,
        "is_active": p.is_active,
        "owner_type": p.owner_type,
        "owner_id": p.owner_id,
        "allowed_gpu_types": list(p.allowed_gpu_types),
        "max_cost_per_hour": p.max_cost_per_hour,
        "is_dedicated": p.is_dedicated,
        "scheduling_policy_json": p.scheduling_policy_json,
        "provider_pool_id": p.provider_pool_id,
        "provider_credential_name": p.provider_credential_name,
        "cluster_id": p.cluster_id,
        "pool_type": p.pool_type,
        "gpu_count": p.gpu_count or 1,
        "lifecycle_state": lifecycle_state,
        "created_at": p.created_at,
        "updated_at": p.updated_at,
    }


class UpdatePoolMetadataRequest(BaseModel):
    """Request body for PATCH /updatepool/{pool_id}.

    Only ``metadata`` is accepted today. Additional mutable fields may be
    added later without breaking callers (the response always returns the
    full pool row).
    """
    metadata: Optional[dict[str, Any]] = None


@router.patch("/updatepool/{pool_id}")
async def update_pool_metadata(pool_id: str, req: UpdatePoolMetadataRequest, request: Request):
    """Merge ``metadata`` into compute_pools.metadata for the given pool.

    If the pool's provider is ``"aws"`` and a non-null metadata dict is
    supplied, the dict is validated against ``AWSPoolMetadata`` before being
    persisted, so AWSAdapter.provision_node never receives a malformed config.
    """
    try:
        pool_uuid = UUID(pool_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid pool_id")

    conn = None
    try:
        conn = await asyncpg.connect(POSTGRES_DSN)
        pool = await conn.fetchrow(
            """
            SELECT id, provider, metadata
            FROM compute_pools
            WHERE id = $1 AND is_active = TRUE
            """,
            pool_uuid,
        )
        if not pool:
            raise HTTPException(status_code=404, detail="Pool not found")
    except HTTPException:
        raise
    except Exception as e:
        if conn:
            await conn.close()
            conn = None
        raise HTTPException(status_code=500, detail=f"DB error: {e}")

    provider = pool["provider"]

    # Validate AWS metadata shape before writing.
    if provider == "aws" and req.metadata is not None:
        from inferia.services.orchestration.services.adapter_engine.adapters.aws.pool_metadata import (
            AWSPoolMetadata,
        )
        from pydantic import ValidationError as _ValidationError
        try:
            AWSPoolMetadata(**req.metadata)
        except _ValidationError as e:
            if conn:
                await conn.close()
            raise HTTPException(status_code=422, detail={"errors": e.errors()})
    elif provider != "aws" and req.metadata is not None:
        # Reject AWS-specific metadata keys sent to non-AWS pools to prevent
        # operator confusion (spec section "Failure modes").
        aws_keys = {"subnet_id", "security_group_ids", "ami_id", "iam_instance_profile"}
        if aws_keys & set(req.metadata.keys()):
            if conn:
                await conn.close()
            raise HTTPException(
                status_code=400,
                detail=f"pool provider is not aws; AWS metadata keys are not valid for provider '{provider}'",
            )

    try:
        if req.metadata is not None:
            # Merge: keep existing keys that are not overridden.
            existing_meta = pool["metadata"] or {}
            if isinstance(existing_meta, str):
                import json as _json
                existing_meta = _json.loads(existing_meta)
            merged = {**existing_meta, **req.metadata}
            await conn.execute(
                """
                UPDATE compute_pools
                SET metadata = $2::jsonb,
                    updated_at = now()
                WHERE id = $1 AND is_active = TRUE
                """,
                pool_uuid,
                json.dumps(merged),
            )

        # Return the updated pool row (metadata only).
        updated = await conn.fetchrow(
            "SELECT id, provider, metadata FROM compute_pools WHERE id = $1",
            pool_uuid,
        )
    finally:
        if conn:
            await conn.close()

    return {
        "pool_id": pool_id,
        "provider": provider,
        "metadata": updated["metadata"] if updated else req.metadata,
        "status": "UPDATED",
    }


@router.post("/stoppool/{pool_id}")
async def stop_pool(pool_id: str):
    try:
        pool_uuid = UUID(pool_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid pool_id")

    conn = None
    try:
        conn = await asyncpg.connect(POSTGRES_DSN)
        pool = await conn.fetchrow(
            """
            SELECT id, lifecycle_state, is_active
            FROM compute_pools
            WHERE id = $1 AND is_active = TRUE
            """,
            pool_uuid,
        )
        if not pool:
            raise HTTPException(status_code=404, detail="Pool not found")

        lifecycle_state = (pool["lifecycle_state"] or POOL_STATE_RUNNING).lower()
        if lifecycle_state == POOL_STATE_TERMINATED:
            return {"pool_id": pool_id, "status": "TERMINATED"}
        if lifecycle_state == POOL_STATE_TERMINATING:
            return {"pool_id": pool_id, "status": "TERMINATING"}

        await conn.execute(
            """
            UPDATE compute_pools
            SET lifecycle_state = $2,
                updated_at = now()
            WHERE id = $1 AND is_active = TRUE
            """,
            pool_uuid,
            POOL_STATE_TERMINATING,
        )
    finally:
        if conn:
            await conn.close()

    await log_audit_event(
        user_id=None,
        action="pool.stop",
        resource_type="compute_pool",
        resource_id=pool_id,
        status="success",
        org_id=await _lookup_org_id("compute_pool", pool_id),
    )

    asyncio.create_task(_terminate_pool_background(pool_uuid))
    return {"pool_id": pool_id, "status": "TERMINATING"}


@router.post("/deletepool/{pool_id}")
async def delete_pool(pool_id: str):
    try:
        pool_uuid = UUID(pool_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid pool_id")

    lifecycle_state = await _get_pool_lifecycle_state(pool_uuid)
    if lifecycle_state is None:
        raise HTTPException(status_code=404, detail="Pool not found")
    if lifecycle_state != POOL_STATE_TERMINATED:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Pool is '{lifecycle_state}'. Stop it first and wait for "
                f"'{POOL_STATE_TERMINATED}' state before deleting."
            ),
        )

    async with _auth_channel() as channel:
        stub = compute_pool_pb2_grpc.ComputePoolManagerStub(channel)

        try:
            await stub.DeletePool(compute_pool_pb2.DeletePoolRequest(pool_id=pool_id))
        except grpc.RpcError as e:
            if e.code() == grpc.StatusCode.NOT_FOUND:
                raise HTTPException(status_code=404, detail="Pool not found")
            if e.code() == grpc.StatusCode.FAILED_PRECONDITION:
                raise HTTPException(status_code=409, detail=e.details())
            raise HTTPException(status_code=500, detail=e.details())

    await log_audit_event(
        user_id=None,
        action="pool.delete",
        resource_type="compute_pool",
        resource_id=pool_id,
        status="success",
        org_id=await _lookup_org_id("compute_pool", pool_id),
    )

    return {"pool_id": pool_id, "status": "DELETED"}


@router.get("/listDeployments/{pool_id}")
async def list_deployments(pool_id: str | None = None):
    """
    List all deployments.
    Optionally filter by pool_id.
    """
    async with _auth_channel() as channel:
        stub = model_deployment_pb2_grpc.ModelDeploymentServiceStub(channel)

        try:
            resp = await stub.ListDeployments(
                model_deployment_pb2.ListDeploymentsRequest(pool_id=pool_id or "")
            )
        except grpc.RpcError as e:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to list deployments: {e.details()}",
            )

    return {
        "deployments": [
            {
                "deployment_id": d.deployment_id,
                "model_name": d.model_name,
                "model_version": d.model_version,
                "state": d.state,
                "replicas": d.replicas,
                "pool_id": d.pool_id,
                "engine": d.engine,
                "endpoint": d.endpoint,
                "org_id": d.org_id,
                "error_message": d.error_message or None,
            }
            for d in resp.deployments
        ]
    }


@router.get("/logs/{deployment_id}")
async def get_deployment_logs(deployment_id: str):
    """
    Fetch logs for a deployment from the backend provider.
    Currently only supports Nosana (via IPFS result).
    """
    from uuid import UUID
    import asyncpg

    try:
        # 1. Get deployment to find the pool/provider
        dep_uuid = UUID(deployment_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid UUID")

    conn = await asyncpg.connect(POSTGRES_DSN)
    try:
        # Get pool_id to identify provider and credential
        dep = await conn.fetchrow(
            """
            SELECT d.pool_id, p.provider, p.provider_credential_name, d.state
            FROM model_deployments d
            JOIN compute_pools p ON d.pool_id = p.id
            WHERE d.deployment_id = $1
            """,
            dep_uuid,
        )

        if not dep:
            raise HTTPException(status_code=404, detail="Deployment/Pool not found")

        provider = dep["provider"]
        provider_credential_name = dep.get("provider_credential_name")

        # 2. Get the Node ID / Provider Instance ID
        # Since compute_inventory lacks deployment_id, we look up via model_deployments.node_ids
        # We fetch the first node ID from the deployment's node_ids array
        dep_nodes = await conn.fetchrow(
            """
            SELECT node_ids
            FROM model_deployments
            WHERE deployment_id = $1
            """,
            dep_uuid,
        )

        if not dep_nodes or not dep_nodes["node_ids"]:
            return {"logs": ["Waiting for node provisioning..."]}

        node_id = dep_nodes["node_ids"][0]

        node = await conn.fetchrow(
            """
             SELECT provider_instance_id
             FROM compute_inventory
             WHERE id = $1
             """,
            node_id,
        )

        if not node:
            return {"logs": ["Node record not found"]}

        provider_instance_id = node["provider_instance_id"]

        # 3. Try adapter first, fall back to ES
        try:
            adapter = get_adapter(provider)
            if hasattr(adapter, "get_logs"):
                logs_data = await adapter.get_logs(
                    provider_instance_id=provider_instance_id,
                    provider_credential_name=provider_credential_name,
                )
                if logs_data and logs_data.get("logs"):
                    return logs_data
        except Exception as e:
            logger.warning(f"Adapter log fetch failed, trying ES fallback: {e}")

        # 4. Fallback: try Elasticsearch persisted logs
        try:
            store = await _get_log_store()
            es_logs = await store.get_logs(deployment_id)
            if es_logs:
                return {"logs": es_logs, "source": "persisted"}
        except Exception as e:
            logger.warning(f"ES log fallback also failed: {e}")

        return {"logs": [f"No logs available for provider: {provider}"], "source": "none"}

    finally:
        await conn.close()


@router.get("/logs/{deployment_id}/persisted")
async def get_persisted_deployment_logs(deployment_id: str):
    """
    Retrieve persisted terminal logs captured on deployment failure/stop.
    Returns the most recent log snapshot.
    """
    from inferia.services.orchestration.repositories.terminal_log_repo import (
        TerminalLogRepository,
    )

    try:
        dep_uuid = UUID(deployment_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid UUID")

    conn = await asyncpg.connect(POSTGRES_DSN)
    try:
        repo = TerminalLogRepository(conn)
        log_entry = await repo.get_by_deployment(dep_uuid)
        if not log_entry:
            return {"logs": [], "message": "No persisted logs found for this deployment"}
        return {
            "logs": log_entry["log_lines"],
            "captured_at": log_entry["captured_at"].isoformat(),
            "trigger_event": log_entry["trigger_event"],
        }
    finally:
        await conn.close()


@router.get("/logs/{deployment_id}/stream")
async def get_deployment_log_stream_info(deployment_id: str, request: Request):
    """
    Get WebSocket connection details for log streaming.
    """
    from uuid import UUID
    import asyncpg

    try:
        dep_uuid = UUID(deployment_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid UUID")

    conn = await asyncpg.connect(POSTGRES_DSN)
    try:
        # 1. Get deployment, provider, and credential name
        dep = await conn.fetchrow(
            """
            SELECT p.provider, p.provider_credential_name, d.node_ids, d.org_id
            FROM model_deployments d
            JOIN compute_pools p ON d.pool_id = p.id
            WHERE d.deployment_id = $1
            """,
            dep_uuid,
        )

        if not dep:
            raise HTTPException(status_code=404, detail="Deployment/Pool not found")

        provider = dep["provider"]
        provider_credential_name = dep.get("provider_credential_name")
        if not dep["node_ids"]:
            return {"error": "No nodes assigned to this deployment yet."}

        node_id = dep["node_ids"][0]

        node = await conn.fetchrow(
            "SELECT provider_instance_id, agent_kind FROM compute_inventory WHERE id = $1", node_id
        )

        if not node:
            return {"error": "Node record not found"}

        provider_instance_id = node["provider_instance_id"]

        # 1.5 — Worker short-circuit. Worker pools don't have a sidecar log
        # stream the way Nosana does; we already expose live container logs
        # via /api/v1/admin/workers/{node_id}/logs?deployment={id}, which is
        # the same WS path the Compute > Nodes > Logs tab uses. Returning
        # the relative URL here lets the dashboard's TerminalLogs component
        # reuse its existing flow (it appends ?token=<jwt> and opens).
        if (provider == "on_prem") or (node.get("agent_kind") == "worker"):
            return {
                "ws_url": (
                    f"/api/v1/admin/workers/{node_id}/logs"
                    f"?deployment={deployment_id}"
                ),
                "subscription": {
                    "type": "subscribe_logs",
                    "provider": "worker",
                    "deployment_id": deployment_id,
                    "node_id": str(node_id),
                },
            }

        # 2. Call Adapter for streaming info
        try:
            adapter = get_adapter(provider)

            # Pass base_url to adapter if it supports it to construct absolute WS URL
            extra_args = {}
            if hasattr(adapter, "get_log_streaming_info"):
                import inspect

                sig = inspect.signature(adapter.get_log_streaming_info)
                via_gateway = (
                    request.headers.get("x-gateway-request", "").lower() == "true"
                )
                if "base_url" in sig.parameters and via_gateway:
                    extra_args["base_url"] = str(request.base_url)
                if (
                    "provider_credential_name" in sig.parameters
                    and provider_credential_name
                ):
                    extra_args["provider_credential_name"] = provider_credential_name

            stream_info = await adapter.get_log_streaming_info(
                provider_instance_id=provider_instance_id, **extra_args
            )
            # Inject deployment_id and org_id into subscription for log persistence
            if isinstance(stream_info, dict) and "subscription" in stream_info:
                stream_info["subscription"]["deployment_id"] = deployment_id
                stream_info["subscription"]["org_id"] = dep.get("org_id", "")
            elif isinstance(stream_info, dict):
                stream_info["deployment_id"] = deployment_id
            return stream_info
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Adapter error: {str(e)}")

    finally:
        await conn.close()


@router.get("/deployments")
async def list_all_deployments(
    org_id: str | None = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """
    List ALL deployments across all pools.
    Optionally filter by org_id.
    """
    import logging

    logger = logging.getLogger("deployment-server")
    logger.info(f"list_all_deployments called for org_id: {org_id}")

    async with _auth_channel() as channel:
        stub = model_deployment_pb2_grpc.ModelDeploymentServiceStub(channel)
        try:
            logger.info("Calling gRPC ListDeployments...")
            resp = await stub.ListDeployments(
                model_deployment_pb2.ListDeploymentsRequest(
                    pool_id="", org_id=org_id or ""
                )
            )
            logger.info(f"gRPC ListDeployments returned {len(resp.deployments)} items")
        except grpc.RpcError as e:
            logger.error(f"gRPC ListDeployments failed: {e.code()} - {e.details()}")
            raise HTTPException(
                status_code=500,
                detail=f"Failed to list all deployments: {e.details()}",
            )

    # Enrich with created_at from DB (not available in gRPC response)
    created_at_map = {}
    conn = None
    try:
        dep_ids = [d.deployment_id for d in resp.deployments]
        if dep_ids:
            conn = await asyncpg.connect(POSTGRES_DSN)
            rows = await conn.fetch(
                "SELECT deployment_id::text, created_at FROM model_deployments WHERE deployment_id = ANY($1::uuid[])",
                dep_ids,
            )
            created_at_map = {
                row["deployment_id"]: row["created_at"].isoformat() if row["created_at"] else None
                for row in rows
            }
    except Exception as e:
        logger.warning(f"Failed to fetch created_at for deployments: {e}")
    finally:
        if conn:
            await conn.close()

    all_deployments = [
        {
            "deployment_id": d.deployment_id,
            "model_name": d.model_name,
            "model_version": d.model_version,
            "state": d.state,
            "replicas": d.replicas,
            "pool_id": d.pool_id,
            "created_at": created_at_map.get(d.deployment_id),
            "engine": d.engine,
            "endpoint": d.endpoint,
            "org_id": d.org_id,
            "error_message": d.error_message or None,
        }
        for d in resp.deployments
    ]

    # Apply server-side pagination
    paginated = all_deployments[offset : offset + limit]
    return {"deployments": paginated, "total": len(all_deployments)}


@router.get("/provider/resources")
async def list_provider_resources(provider: str | None = None):
    """
    List available resources for a specific provider or all registered providers.

    Args:
        provider: Optional provider name. If not specified, returns resources from all providers.

    Returns:
        Dict with "resources" key containing list of available resources.
    """
    from inferia.services.orchestration.services.adapter_engine.registry import (
        ADAPTER_REGISTRY,
    )

    try:
        if provider:
            # Get resources for specific provider
            adapter = get_adapter(provider)
            resources = await adapter.discover_resources()
            return {"resources": resources, "provider": provider}
        else:
            # Get resources from all registered providers
            all_resources = []
            errors = []

            for provider_name in ADAPTER_REGISTRY.keys():
                try:
                    adapter = get_adapter(provider_name)
                    provider_resources = await adapter.discover_resources()
                    # Tag each resource with its provider
                    for resource in provider_resources:
                        resource["_provider"] = provider_name
                    all_resources.extend(provider_resources)
                except Exception as e:
                    errors.append({"provider": provider_name, "error": str(e)})

            response = {"resources": all_resources}
            if errors:
                response["errors"] = errors
            return response

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Failed to discover resources: {str(e)}"
        )


@router.post("/registerModel")
async def register_model(req: ModelRegistryRequest):
    async with _auth_channel() as channel:
        stub = model_registry_pb2_grpc.ModelRegistryServiceStub(channel)

        try:
            resp = await stub.RegisterModel(
                model_registry_pb2.RegisterModelRequest(
                    name=req.model_name,
                    version=req.model_version,
                    backend=req.backend,
                    artifact_uri=req.artifact_uri,
                    config_json=json.dumps(req.config_json),
                )
            )
        except grpc.RpcError as e:
            raise HTTPException(
                status_code=500,
                detail=f"Model registration failed: {e.details()}",
            )

    return {
        "model_id": resp.model_id,
        "status": "REGISTERED",
    }


@router.get("/getModel/{model_name}/{model_version}")
async def get_model(model_name: str, model_version: str):
    async with _auth_channel() as channel:
        stub = model_registry_pb2_grpc.ModelRegistryServiceStub(channel)

        try:
            resp = await stub.GetModel(
                model_registry_pb2.GetModelRequest(
                    name=model_name,
                    version=model_version,
                )
            )
        except grpc.RpcError as e:
            raise HTTPException(
                status_code=404,
                detail=f"Model not found: {e.details()}",
            )

    return {
        "model_id": resp.model_id,
        "model_name": resp.name,
        "model_version": resp.version,
        "backend": resp.backend,
        "artifact_uri": resp.artifact_uri,
        "config_json": json.loads(resp.config_json),
    }


@router.delete("/deleteModel")
async def delete_model(req: DeleteModelRequest):
    async with _auth_channel() as channel:
        stub = model_registry_pb2_grpc.ModelRegistryServiceStub(channel)

        try:
            await stub.DeleteModel(
                model_registry_pb2.DeleteModelRequest(model_id=req.model_id)
            )
        except grpc.RpcError as e:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to delete model: {e.details()}",
            )

    return {
        "model_id": req.model_id,
        "status": "DELETED",
    }


@router.get("/listModels/{model_name}")
async def list_models(model_name: str | None = None):
    async with _auth_channel() as channel:
        stub = model_registry_pb2_grpc.ModelRegistryServiceStub(channel)

        try:
            resp = await stub.ListModels(
                model_registry_pb2.ListModelsRequest(name=model_name)
            )
        except grpc.RpcError as e:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to list models: {e.details()}",
            )

    return {
        "models": [
            {
                "model_id": m.model_id,
                "model_name": m.name,
                "model_version": m.version,
                "backend": m.backend,
                "artifact_uri": m.artifact_uri,
                "config_json": json.loads(m.config_json),
            }
            for m in resp.models
        ]
    }


@router.websocket("/ws")
async def websocket_logs_endpoint(websocket: WebSocket):
    """
    WebSocket endpoint for log streaming.
    Supported providers: skypilot
    """
    await websocket.accept()
    logger.info("WebSocket connection accepted")

    stream_task = None
    process = None

    try:
        # 1. Wait for subscription message
        message = await websocket.receive_text()
        data = json.loads(message)

        if data.get("type") != "subscribe_logs":
            await websocket.send_json(
                {"type": "error", "message": "First message must be a subscription"}
            )
            await websocket.close()
            return

        provider = data.get("provider")

        # Import asyncio for both branches
        import asyncio

        if provider == "skypilot":
            cluster_id = data.get("cluster_id")
            service_name = data.get("service_name")

            if not cluster_id or not service_name:
                await websocket.send_json(
                    {"type": "error", "message": "Missing cluster_id or service_name"}
                )
                await websocket.close()
                return

            logger.info(
                f"Streaming logs for SkyPilot cluster {cluster_id}, service {service_name}"
            )

            # Start streaming process
            # Use 'sky exec' to tail docker logs
            cmd = [
                "sky",
                "exec",
                cluster_id,
                "--",
                f"docker logs --tail 100 -f {service_name}",
            ]

            process = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
            )

            # Create log buffer for persistence
            log_buffer = await _create_log_buffer(deployment_id=data.get("deployment_id", "unknown"), org_id=data.get("org_id", ""))
            await log_buffer.start_periodic_flush()

            async def read_logs():
                try:
                    while True:
                        line = await process.stdout.readline()
                        if not line:
                            break
                        decoded = line.decode().strip()
                        log_buffer.append(decoded)
                        await websocket.send_json(
                            {"type": "log", "data": decoded}
                        )
                except asyncio.CancelledError:
                    pass
                except Exception as e:
                    logger.error(f"Error reading logs: {e}")

            stream_task = asyncio.create_task(read_logs())

            try:
                # Wait for client to close or process to end
                while True:
                    try:
                        await websocket.receive_text()
                    except WebSocketDisconnect:
                        break
            finally:
                await log_buffer.stop()

        elif provider == "nosana":
            job_id = data.get("jobId")
            node_address = data.get("nodeAddress")
            credential_name = data.get("credentialName")

            if not job_id or not node_address or node_address == "none":
                await websocket.send_json(
                    {"type": "error", "message": "Missing jobId or nodeAddress"}
                )
                await websocket.close()
                return

            logger.info(
                f"Streaming logs for Nosana job {job_id} on node {node_address} with credential {credential_name}"
            )

            # Get the Nosana API key and signature
            try:
                api_key = await _get_nosana_api_key(credential_name)
                signature = await _get_nosana_signature(api_key)
            except Exception as e:
                logger.error(f"Failed to get Nosana credentials: {e}")
                await websocket.send_json(
                    {
                        "type": "error",
                        "message": f"Failed to authenticate with Nosana: {e}",
                    }
                )
                await websocket.close()
                return

            # Connect to Nosana node WebSocket directly (not sidecar)
            # Format: wss://<node_address>.<ingress_domain>/flog
            import websockets

            ws_url = f"wss://{node_address}.{NOSANA_INGRESS_DOMAIN}/flog"
            auth_header = f"nosana-auth:{signature}"

            headers = {"Authorization": auth_header}

            subscribe_msg = {
                "path": "/flog",
                "headers": {"Authorization": auth_header},
                "header": auth_header,
                "body": {"jobAddress": job_id, "address": node_address},
            }

            logger.info(f"Connecting to Nosana WS: {ws_url}")

            try:
                async with websockets.connect(
                    ws_url,
                    additional_headers=headers,
                ) as sidecar_ws:
                    # Send subscription message to Nosana node
                    await sidecar_ws.send(json.dumps(subscribe_msg))

                    logger.info("Connected to Nosana node, relaying logs...")

                    # Create log buffer for persistence
                    log_buffer = await _create_log_buffer(
                        deployment_id=data.get("deployment_id", job_id),
                        org_id=data.get("org_id", ""),
                    )
                    await log_buffer.start_periodic_flush()

                    async def client_to_sidecar():
                        while True:
                            payload = await websocket.receive()
                            event_type = payload.get("type")
                            if event_type == "websocket.disconnect":
                                break
                            if payload.get("text"):
                                await sidecar_ws.send(payload["text"])

                    async def sidecar_to_client():
                        async for msg in sidecar_ws:
                            if isinstance(msg, bytes):
                                decoded = msg.decode("utf-8", errors="replace")
                                log_buffer.append(decoded)
                                await websocket.send_json(
                                    {"type": "log", "data": decoded}
                                )
                            else:
                                try:
                                    parsed = json.loads(msg)
                                    if isinstance(parsed, dict) and "data" in parsed:
                                        log_data = parsed["data"]
                                    else:
                                        log_data = msg
                                except json.JSONDecodeError:
                                    log_data = msg
                                log_buffer.append(str(log_data))
                                await websocket.send_json(
                                    {"type": "log", "data": log_data}
                                )

                    tasks = {
                        asyncio.create_task(client_to_sidecar()),
                        asyncio.create_task(sidecar_to_client()),
                    }
                    try:
                        done, pending = await asyncio.wait(
                            tasks, return_when=asyncio.FIRST_COMPLETED
                        )
                        for task in pending:
                            task.cancel()
                        for task in done:
                            exc = task.exception()
                            if exc:
                                logger.error(f"Nosana WS task error: {exc}")
                    finally:
                        await log_buffer.stop()

            except Exception as e:
                logger.error(f"Nosana WebSocket error: {e}")
                await websocket.send_json(
                    {
                        "type": "error",
                        "message": f"Failed to connect to Nosana sidecar: {e}",
                    }
                )
                await websocket.close()

        else:
            await websocket.send_json(
                {
                    "type": "error",
                    "message": f"Provider {provider} not supported for direct streaming",
                }
            )
            await websocket.close()

    except WebSocketDisconnect:
        logger.info("WebSocket client disconnected")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        try:
            await websocket.send_json({"type": "error", "message": str(e)})
        except:
            pass
    finally:
        if stream_task:
            stream_task.cancel()
        if process and process.returncode is None:
            try:
                process.terminate()
            except:
                pass
        logger.info("WebSocket connection closed")

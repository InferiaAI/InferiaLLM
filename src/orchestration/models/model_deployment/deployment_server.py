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

from orchestration.v1 import (
    model_deployment_pb2,
    model_deployment_pb2_grpc,
    model_registry_pb2,
    model_registry_pb2_grpc,
    compute_pool_pb2,
    compute_pool_pb2_grpc,
)

from orchestration.repositories.provider_repo import (
    ProviderResourceRepository,
)
from orchestration.provisioning.engine.registry import (
    get_adapter,
    ADAPTER_REGISTRY,
)
from orchestration.models.model_deployment.model_ref import (
    resolve_artifact_uri,
)
from orchestration.models.model_deployment.log_store import (
    DeploymentLogStore,
    DeploymentLogBuffer,
)
from orchestration.config import settings as orch_settings
from typing import Any, Optional
from types import SimpleNamespace

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
    from orchestration.config import settings

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


def _source_field(source, name: str):
    """Read ``name`` from a load-spec source that is EITHER a Pydantic model
    (attribute access, e.g. ``DeployModelRequest``) OR an asyncpg
    Record / dict (key access, e.g. a model_deployments row).

    Returns ``None`` when the field is absent on either shape.
    """
    if isinstance(source, dict):
        return source.get(name)
    # asyncpg.Record supports mapping membership/indexing; use `in` + `[]`
    # (rather than `.get()`) so this works for any Mapping-like source. Guard
    # via keys() to confirm the source is mapping-shaped before the membership
    # test, falling back to attribute access for Pydantic models.
    keys = getattr(source, "keys", None)
    if callable(keys):
        try:
            if name in source:  # Record/Mapping membership
                return source[name]
            return None
        except TypeError:
            pass
    return getattr(source, name, None)


def _model_spec_from_source(load_spec_source) -> dict:
    """Build the ``spec["model"]`` dict from a load-spec source.

    The source is EITHER a :class:`DeployModelRequest` (deploy path) OR a
    deployment DB row (asyncpg Record / dict, resume path). Both expose
    ``engine`` / ``configuration`` / ``inference_model`` / ``model_name``.

    Extracts artifact_uri / format / backend either from a nested
    ``configuration`` or falls back to the top-level engine / inference_model
    fields.

    Raises HTTPException(400) if no artifact_uri can be resolved.
    """
    engine = _source_field(load_spec_source, "engine")
    configuration = _source_field(load_spec_source, "configuration")
    inference_model = _source_field(load_spec_source, "inference_model")
    model_name = _source_field(load_spec_source, "model_name")

    cfg = configuration or {}
    # A DB row may carry configuration as a JSON string (jsonb -> str under
    # asyncpg); decode so the warm path resolves identically to /deploy.
    if isinstance(cfg, str):
        try:
            cfg = json.loads(cfg)
        except (ValueError, TypeError):
            cfg = {}
    if not isinstance(cfg, dict):
        cfg = {}
    model_block = cfg.get("model") if isinstance(cfg.get("model"), dict) else {}
    # resolve_artifact_uri also reads cfg["model_id"] (where ollama/localai
    # carry the bare name:tag) and guarantees a worker-acceptable scheme, so
    # the warm path and the EC2-bootstrap path resolve identically.
    artifact_uri = resolve_artifact_uri(
        configuration=cfg,
        inference_model=inference_model,
        model_name=model_name,
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
            or engine
            or "vllm"
        ),
    }


def _model_spec_from_request(req: "DeployModelRequest") -> dict:
    """Backward-compatible wrapper: build ``spec["model"]`` from a deploy
    request. Delegates to :func:`_model_spec_from_source`."""
    return _model_spec_from_source(req)


async def _build_provisioning_spec(
    *, pool_row, pool_meta: dict, decision, org_id, ami_id: str | None = None,
) -> dict:
    """Build the provisioning-job spec for a ColdStart enqueue.

    For AWS this derives the full spec the reconciler's PreflightHandler +
    PulumiUpHandler need:
      - instance_type: pool.allowed_gpu_types[0] (fallback: tail of
        provider_pool_id, or pool_meta.instance_type)
      - instance_class: derived from the AWS instance catalog (single source
        of truth — never trust a separately-stored value)
      - region: pool.region_constraint[0] -> pool_meta.region (required; 422 if absent)
      - optional per-pool overrides (subnet/SG/IAM/AMI/root volume/image tag)

    Raises HTTPException(422) when instance_type / instance_class / region
    cannot be resolved, so the operator gets an actionable error at deploy
    time instead of an opaque INVALID_SPEC on the async job they never see.

    For non-AWS providers it returns the legacy minimal spec (those clouds
    still use the direct-adapter provisioning path, not this reconciler).
    """
    provider = (getattr(decision, "provider", None) or "aws").lower()
    gpu_count = getattr(decision, "gpu_total_per_node", None) or 0
    org_str = str(org_id) if org_id else None
    pool_id = pool_row.get("id") if hasattr(pool_row, "get") else pool_row["id"]

    if provider != "aws":
        return {
            "provider": provider,
            "pool_id": str(pool_id),
            "org_id": org_str,
            "instance_type": pool_meta.get("instance_type"),
            "region": pool_meta.get("region"),
            "gpu_count": gpu_count,
        }

    from providers.aws.instance_catalog import (
        lookup as _catalog_lookup,
    )

    allowed = list(pool_row.get("allowed_gpu_types") or [])
    instance_type = (allowed[0] if allowed else None) or pool_meta.get("instance_type")
    if not instance_type:
        ppid = pool_row.get("provider_pool_id") or ""
        if "/" in ppid:
            instance_type = ppid.rsplit("/", 1)[-1]
    if not instance_type:
        raise HTTPException(
            status_code=422,
            detail="AWS pool has no instance type configured (allowed_gpu_types)",
        )

    it = _catalog_lookup(instance_type)
    if it is None:
        raise HTTPException(
            status_code=422,
            detail=(f"instance type {instance_type!r} is not in the AWS "
                    f"catalog; pick a supported type when creating the pool"),
        )
    instance_class = it.cls

    region = None
    rc = pool_row.get("region_constraint")
    if rc:
        region = (rc[0] if isinstance(rc, (list, tuple)) else rc) or None
    region = region or pool_meta.get("region")
    if not region:
        raise HTTPException(
            status_code=422,
            detail="AWS pool has no region (set region_constraint at pool creation)",
        )

    spec = {
        "provider": "aws",
        "pool_id": str(pool_id),
        "org_id": org_str,
        "instance_type": instance_type,
        "instance_class": instance_class,
        "region": region,
        "gpu_count": gpu_count,
    }
    # Optional per-pool overrides — only included when set so build_ec2_program
    # falls back to its safe defaults (synthesised SG, default VPC, etc.).
    for key in ("subnet_id", "security_group_ids", "security_group_id",
                "iam_instance_profile", "ami_id", "root_volume_gb",
                "worker_image_tag"):
        val = pool_meta.get(key)
        if val not in (None, "", []):
            spec[key] = val
    # Per-deploy ami_id override (from DeployModelRequest.ami_id, required for
    # vLLM). Takes priority over pool_meta.ami_id set above (deploy-level wins).
    if ami_id:
        spec["ami_id"] = ami_id
    # Root volume: GPU classes boot a Deep Learning AMI whose backing
    # snapshot is ~75GB+, so the build_program default of 50GB fails with
    # InvalidBlockDeviceMapping ("Volume ... smaller than snapshot, expect
    # >= 75GB"). Default GPU tiers to 100GB; CPU (plain Ubuntu) to 30GB.
    # A per-pool root_volume_gb override above still wins.
    if "root_volume_gb" not in spec:
        spec["root_volume_gb"] = 30 if instance_class == "cpu" else 100
    return spec


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
    """Mark the node terminating and flip its provisioning job to 'cancelling'
    so the reconciler's CancelHandler runs `pulumi destroy` on the real stack
    ``inferia-<node_id>``.

    Uses ``force_cancel`` (the same mechanism as DELETE /nodes/{id}) — NOT
    ``enqueue``. Enqueue inserts a NEW job at phase='preflight', which the
    reconciler routes to PreflightHandler (it tries to PROVISION, never
    destroys) -> the EC2 leaked on every refcount=0 terminate. force_cancel
    flips the EXISTING job (from any phase incl. READY/FAILED) to 'cancelling'.

    Returns True on success (job flipped, or already cancelling/terminated =
    destroy already in flight); False only if force_cancel raised.

    When force_cancel raises (we return False), we STRIP the
    ``terminating`` / ``terminating_at`` flags we just stamped — no destroy
    is in flight, so leaving the node flagged 'terminating' would make it
    render that way forever in the dashboard with nothing actually tearing
    it down. The periodic reaper re-arms the real teardown for such a node;
    clearing the flag here just avoids a misleading UI in the interim.
    """
    async with db_pool.acquire() as conn:
        await conn.execute(
            "UPDATE compute_inventory SET metadata = "
            "jsonb_set(COALESCE(metadata,'{}'::jsonb), "
            "'{terminating}', 'true'::jsonb) WHERE id=$1",
            node_id,
        )
    try:
        flipped = await jobs_repo.force_cancel(node_id=node_id)
        if not flipped:
            # No non-terminal job to flip: the node's job is already
            # cancelling/terminated (destroy in progress/done) or there is no
            # reconciler job for it. Idempotent — nothing more to do.
            logger.info(
                "_initiate_node_destroy: no live job to cancel for node=%s "
                "(already terminating, or no reconciler job)", node_id,
            )
        return True
    except Exception as e:
        logger.exception(
            "_initiate_node_destroy: force_cancel failed for node=%s: %s",
            node_id, e,
        )
        # The destroy did NOT enqueue. Roll back the 'terminating' stamp so
        # the node doesn't display terminating forever with no destroy in
        # flight (the reaper will re-arm the teardown on its next tick).
        try:
            async with db_pool.acquire() as conn:
                await conn.execute(
                    "UPDATE compute_inventory SET metadata = "
                    "(COALESCE(metadata,'{}'::jsonb) "
                    "- 'terminating' - 'terminating_at'), updated_at = now() "
                    "WHERE id=$1",
                    node_id,
                )
        except Exception:
            logger.warning(
                "_initiate_node_destroy: failed to clear terminating flag for "
                "node=%s after enqueue failure", node_id, exc_info=True,
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
    ami_id: str | None = None
    hf_token_name: str | None = None


class PreflightRequest(BaseModel):
    model_id: str
    hf_token: str | None = None
    hf_token_name: str | None = None
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
    region_constraint: list[str] | None = None  # e.g. ["us-east-1"]; the region EC2/Pulumi launches in
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

        # Job pools (the AWS pool-first path) own per-node EC2 stacks created
        # by the reconciler. Stopping the pool must release them: flip every
        # live provisioning job to 'cancelling' so the reconciler's
        # CancelHandler destroys each inferia-<node_id> stack. Without this a
        # stopped pool keeps billing. No-op for providers with no jobs.
        await conn.execute(
            """
            UPDATE provisioning_jobs
            SET phase = 'cancelling',
                next_attempt_after = NULL,
                lease_holder = NULL,
                lease_expires_at = NULL,
                updated_at = now()
            WHERE pool_id = $1
              AND phase NOT IN ('cancelling', 'terminated')
            """,
            pool_id,
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
    from orchestration.models.model_deployment.preflight import (
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

    # Resolve HF token: prefer explicit raw token; fall back to named token from store
    _hf_token = req.hf_token
    if not _hf_token and req.hf_token_name:
        from orchestration.models.model_deployment.hf_token_resolver import (
            resolve_hf_token,
        )
        _hf_token = await resolve_hf_token(req.hf_token_name)

    # Skip HF checks for external engines
    external_engines = {"openai", "anthropic", "gemini", "groq", "cerebras", "mistral", "deepseek", "custom"}
    is_external = req.engine and req.engine.lower() in external_engines

    # Engines with their own model registry (ollama, localai) use model:tag format,
    # not HuggingFace org/model — skip all HF-based checks for these
    uses_own_registry = req.engine and req.engine.lower() in ENGINES_WITH_OWN_REGISTRY

    hf_info = None  # Shared metadata for multiple checks

    if not is_external and not uses_own_registry and req.model_id:
        # Check 1: Model accessibility
        result = await check_model_accessibility(req.model_id, _hf_token)
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
            hf_info = await fetch_hf_model_info(req.model_id, _hf_token)

        # Check 2: Model format compatibility
        if hf_info and req.engine:
            fmt = await check_model_format(req.model_id, req.engine, _hf_token)
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


async def place_and_provision(
    *,
    deploy_id,
    pool_id,
    pool_row,
    pool_meta,
    gpu_per_replica,
    org_id,
    engine,
    load_spec_source,
    deps,
    ami_id: str | None = None,
) -> tuple[dict, int]:
    """Place a deployment on a pool and provision compute for it.

    This is the placement+provisioning core lifted verbatim out of the
    ``/deploy`` handler so the resume (``/start``) path can reuse it. It runs
    the transactional ``PoolPlacer.place`` decision (BindToReady /
    CoWaitOnProvisioning / ColdStart / PoolAtCapacity), then post-tx enqueues a
    provisioning job (ColdStart) or fires ``controller.load_model`` (warm path).

    Returns ``(response_body, response_status)``:
      - 200 when the warm path bound to a ready node (DEPLOYING),
      - 202 when a placeholder is created / co-waiting (PENDING_NODE),
      - 503 when the pool is at capacity (POOL_AT_CAPACITY body).

    Raises the same ``HTTPException`` as the inlined ``/deploy`` logic.

    ``deps`` is a ``SimpleNamespace`` carrying ``db_pool``, ``controller``,
    ``inventory``, ``deploys``, ``placer`` and ``jobs_repo``.
    ``load_spec_source`` replaces ``req`` for building the warm load spec — it
    is a :class:`DeployModelRequest` on the deploy path and a deployment DB row
    on the resume path.
    """
    from orchestration.models.model_deployment.pool_placer import (
        BindToReady, CoWaitOnProvisioning, ColdStart, PoolAtCapacity,
    )

    db_pool = deps.db_pool
    controller = deps.controller
    inventory = deps.inventory
    deploys = deps.deploys
    placer = deps.placer
    jobs_repo = deps.jobs_repo

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
                    pool_id=pool_id,
                    gpu_required=gpu_per_replica or 0,
                    tx=conn,
                )

                if isinstance(decision, BindToReady):
                    ok = await inventory.allocate_gpu(
                        decision.node_id, gpu_per_replica or 0, tx=conn,
                    )
                    if not ok:
                        # capacity raced; one retry inside the same tx
                        decision = await placer.place(
                            pool_id=pool_id,
                            gpu_required=gpu_per_replica or 0,
                            tx=conn,
                        )
                        if isinstance(decision, BindToReady):
                            ok = await inventory.allocate_gpu(
                                decision.node_id, gpu_per_replica or 0,
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
                                decision.node_id, gpu_per_replica or 0,
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
                                pool_id=pool_id,
                                gpu_total=decision.gpu_total_per_node,
                                initial_alloc=gpu_per_replica or 0,
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
                                    "pool_id": pool_id,
                                    "org_id": str(org_id) if org_id else "",
                                    "provider": decision.provider,
                                    "spec": await _build_provisioning_spec(
                                        pool_row=pool_row, pool_meta=pool_meta,
                                        decision=decision, org_id=org_id,
                                        ami_id=ami_id,
                                    ),
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
                        decision.node_id, gpu_per_replica or 0, tx=conn,
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
                        pool_id=pool_id,
                        gpu_total=decision.gpu_total_per_node,
                        initial_alloc=gpu_per_replica or 0,
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
                            "pool_id": pool_id,
                            "org_id": str(org_id) if org_id else "",
                            "provider": decision.provider,
                            "spec": await _build_provisioning_spec(
                                pool_row=pool_row, pool_meta=pool_meta,
                                decision=decision, org_id=org_id,
                                ami_id=ami_id,
                            ),
                        }

    except PoolAtCapacity as e:
        await deploys.set_state(deploy_id, "FAILED")
        return (
            {
                "error": "POOL_AT_CAPACITY",
                "current_nodes": e.current_nodes,
                "max_nodes": e.max_nodes,
                "deployment_id": str(deploy_id),
            },
            503,
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
                        pending_enqueue["node_id"], gpu_per_replica or 0,
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
            _cfg = _source_field(load_spec_source, "configuration") or {}
            if isinstance(_cfg, str):
                try:
                    _cfg = json.loads(_cfg)
                except (ValueError, TypeError):
                    _cfg = {}
            if not isinstance(_cfg, dict):
                _cfg = {}
            spec = {
                "deployment_id": str(deploy_id),
                "recipe": (engine or "vllm"),
                "model": _model_spec_from_source(load_spec_source),
                "config": _cfg.get("config") or {},
                "gpu_indices": list(range(gpu_per_replica or 0)),
                "port": 0,
                # Propagate configuration.env (e.g. HF_TOKEN injected by the
                # deploy handler for hf_token_name) to the warm-path load spec.
                "env": dict(_cfg.get("env") or {}),
            }
            try:
                from orchestration.config import settings as _s
                _mirror_base = getattr(_s, "model_mirror_base", "") or ""
                from orchestration.models.model_deployment.mirror_decision import (
                    resolve_and_apply_mirror,
                )
                from orchestration.models.model_cache import deps as _mc_deps
                await resolve_and_apply_mirror(
                    spec, recipe=spec["recipe"],
                    artifact_uri=spec["model"]["artifact_uri"],
                    mirror_base=_mirror_base, cache_repo=_mc_deps.get("repo"),
                )
            except Exception:
                pass  # mirror is best-effort; never block a warm deploy
            result = await controller.load_model(
                node_id=str(node_id), spec=spec,
            )
        except Exception as exc:
            logger.exception("deploy: load_model failed for %s: %s", deploy_id, exc)
            # Rollback: release the GPU and mark FAILED
            async with db_pool.acquire() as conn:
                async with conn.transaction():
                    await inventory.release_gpu(
                        node_id, gpu_per_replica or 0, tx=conn,
                    )
                    await deploys.set_state(deploy_id, "FAILED", tx=conn)
            raise HTTPException(status_code=502,
                                  detail=f"load_model failed: {exc}")

        # controller.load_model AWAITS the worker's reply and returns a
        # CommandResultBody verbatim. The worker can report status='failed'
        # (container create/start failed, readiness-probe timeout, vLLM engine
        # crash) WITHOUT raising — the except above only catches transport
        # errors (NodeUnreachable / timeout / ValueError). We must act on the
        # returned status, mirroring the cold-path linker, or a failed warm
        # load leaves the deploy stuck DEPLOYING forever (no container, no
        # error) and a successful one is never promoted to RUNNING.
        if getattr(result, "status", None) == "failed":
            detail = getattr(result, "detail", "") or "load_model returned status=failed"
            logger.error(
                "deploy: load_model returned status=failed for %s: %s",
                deploy_id, detail,
            )
            # Atomic rollback: release GPU + mark FAILED. Use update_state (not
            # set_state) so the worker's error reaches the dashboard/API via
            # error_message + the published state-change event.
            async with db_pool.acquire() as conn:
                async with conn.transaction():
                    await inventory.release_gpu(
                        node_id, gpu_per_replica or 0, tx=conn,
                    )
                    await deploys.update_state(
                        deploy_id, "FAILED", tx=conn,
                        error_message=detail,
                    )
            raise HTTPException(
                status_code=502,
                detail=f"load_model failed: {detail}",
            )

        # status=ok: the model is serving. Promote DEPLOYING -> RUNNING and
        # publish the inference endpoint so the data plane can route to this
        # worker. We publish the node's CP-reachable advertise_url — NOT the
        # worker's reported endpoint_url, which is a 127.0.0.1:<port> loopback
        # useless to the control plane (mirrors the linker success branch).
        async with db_pool.acquire() as conn:
            await deploys.set_state(deploy_id, "RUNNING", tx=conn)
        response_body["state"] = "RUNNING"
        try:
            async with db_pool.acquire() as conn:
                advertise = await conn.fetchval(
                    "SELECT advertise_url FROM compute_inventory WHERE id=$1",
                    node_id,
                )
            if advertise:
                await deploys.update_endpoint(deploy_id, advertise)
            else:
                logger.warning(
                    "deploy: node=%s has no advertise_url; deploy=%s endpoint "
                    "not set (inference unreachable)",
                    node_id, deploy_id,
                )
        except Exception:
            # Endpoint publish is best-effort — never fail the deploy on it
            # (mirrors the linker, which wraps this in try/except).
            logger.exception(
                "deploy: failed to set endpoint for deploy=%s", deploy_id,
            )

    return response_body, response_status


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
    from orchestration.models.model_deployment.pool_placer import (
        PoolPlacer,
    )
    from orchestration.repositories.inventory_repo import (
        InventoryRepository,
    )
    from orchestration.repositories.model_deployment_repo import (
        ModelDeploymentRepository,
    )
    from orchestration.repositories.pool_repo import (
        ComputePoolRepository,
    )
    from orchestration.state_machine.jobs.repository import (
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

    # 1b. vLLM requires an explicit ami_id (the DLAMI to boot); reject early
    # so the operator sees an actionable 422 instead of a provisioning failure.
    if (req.engine or "").lower() == "vllm" and not req.ami_id:
        raise HTTPException(
            status_code=422,
            detail="ami_id is required for vLLM deployments",
        )

    # 1c. Persist ami_id into configuration so the /start resume path can
    # reuse the operator's selected AMI instead of falling back to resolve_ami.
    if req.ami_id:
        cfg = dict(req.configuration or {})
        cfg["ami_id"] = req.ami_id
        req.configuration = cfg

    # 1d. Resolve a named HF token server-side and inject HF_TOKEN into the
    # worker config.  The client sends only the *name*, never the raw value.
    if req.hf_token_name:
        from orchestration.models.model_deployment.hf_token_resolver import (
            resolve_hf_token,
        )
        _tok = await resolve_hf_token(req.hf_token_name)
        if _tok:
            cfg = dict(req.configuration or {})
            env = dict(cfg.get("env") or {})
            env.setdefault("HF_TOKEN", _tok)  # don't clobber an explicit one
            cfg["env"] = env
            req.configuration = cfg

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

    # 2b. Fire-and-forget pre-warm: start downloading weights to the CP cache
    # in parallel with EC2 provisioning so the model is ready when the worker
    # connects.  Best-effort: never block the deploy on cache failures.
    try:
        from orchestration.models.model_cache import deps as _mc_deps
        from orchestration.models.model_deployment.mirror_decision import (
            derive_cache_key,
        )
        _dl = _mc_deps.get("downloader")
        # Key the pre-warm off the SAME (resolve_artifact_uri -> derive_cache_key)
        # chain the mirror decision uses (resolve_and_apply_mirror), so the row
        # the pre-warm writes is found by the mirror lookup. Keying off
        # req.inference_model directly diverged (scheme prefix on HF, or a
        # display name vs configuration.model_id on ollama) -> get_by_key miss
        # -> worker re-downloaded from origin instead of the CP mirror.
        _uri = resolve_artifact_uri(
            configuration=req.configuration,
            inference_model=req.inference_model,
            model_name=req.model_name,
        )
        if _dl and _uri and (req.engine or "vllm") in ("vllm", "tei", "infinity", "ollama"):
            _src, _mid, _rev = derive_cache_key(req.engine or "vllm", str(_uri))
            _dl.start(source=_src, model_id=_mid, revision=_rev, engine_hint=req.engine)
    except Exception:
        pass  # pre-warm is best-effort; never block a deploy

    # 3. Placement + provisioning (extracted, reused by /start resume).
    deps = SimpleNamespace(
        db_pool=db_pool,
        controller=controller,
        inventory=inventory,
        deploys=deploys,
        placer=placer,
        jobs_repo=jobs_repo,
    )
    response_body, response_status = await place_and_provision(
        deploy_id=deploy_id,
        pool_id=pool_id_uuid,
        pool_row=pool_row,
        pool_meta=pool_meta,
        gpu_per_replica=req.gpu_per_replica,
        org_id=req.org_id,
        engine=req.engine,
        load_spec_source=req,
        deps=deps,
        ami_id=req.ami_id,
    )

    from fastapi.responses import JSONResponse
    # PoolAtCapacity surfaces as a 503 POOL_AT_CAPACITY body from
    # place_and_provision. The pre-extraction code returned early from the
    # except-block WITHOUT writing a success audit event, so mirror that: skip
    # the success audit and return with the Retry-After hint.
    if response_status == 503 and response_body.get("error") == "POOL_AT_CAPACITY":
        return JSONResponse(
            status_code=response_status,
            content=response_body,
            headers={"Retry-After": "60"},
        )

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

    # Fetch node_ids directly from the DB (not in proto response).
    node_ids: list[str] = []
    target_node_id: str | None = None
    try:
        conn = await asyncpg.connect(POSTGRES_DSN)
        try:
            row = await conn.fetchrow(
                "SELECT node_ids, target_node_id FROM model_deployments WHERE deployment_id = $1",
                deployment_id,
            )
            if row:
                if row["node_ids"]:
                    node_ids = [str(n) for n in row["node_ids"]]
                if row["target_node_id"]:
                    target_node_id = str(row["target_node_id"])
        finally:
            await conn.close()
    except Exception:
        logger.warning("failed to fetch node_ids for deployment %s", deployment_id, exc_info=True)

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
        "node_ids": node_ids,
        "target_node_id": node_ids[0] if node_ids else target_node_id,
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


def _build_terminate_deps(
    db_pool,
    *,
    controller=None,
    event_bus=None,
    inventory=None,
    deploys=None,
    pool_repo=None,
    jobs_repo=None,
) -> SimpleNamespace:
    """Construct the ``deps`` namespace ``terminate_deployment_core`` needs.

    Both the REST ``/terminate`` route (which has ``request.app.state``) and the
    gRPC delete path (``worker.handle_terminate_requested``, which only holds its
    own repos) call this so the two entrypoints share ONE refcount-aware
    teardown. Any repo not supplied is built from ``db_pool`` — the gRPC path
    passes the repos it already owns and lets the rest be constructed here.
    """
    from orchestration.repositories.inventory_repo import (
        InventoryRepository,
    )
    from orchestration.repositories.model_deployment_repo import (
        ModelDeploymentRepository,
    )
    from orchestration.repositories.pool_repo import (
        ComputePoolRepository,
    )
    from orchestration.state_machine.jobs.repository import (
        ProvisioningJobRepository,
    )

    return SimpleNamespace(
        db_pool=db_pool,
        controller=controller,
        event_bus=event_bus,
        inventory=inventory if inventory is not None else InventoryRepository(db_pool),
        deploys=deploys
        if deploys is not None
        else ModelDeploymentRepository(db_pool, event_bus=event_bus),
        pool_repo=pool_repo
        if pool_repo is not None
        else ComputePoolRepository(db_pool),
        jobs_repo=jobs_repo
        if jobs_repo is not None
        else ProvisioningJobRepository(db_pool),
    )


async def terminate_deployment_core(deploy_uuid, *, deps) -> dict:
    """Refcount-aware deploy termination (shared by REST + gRPC delete paths).

    PENDING_NODE: unbind + release_gpu; if refcount=0 -> destroy node.
    DEPLOYING/RUNNING: unload_model + release_gpu; if refcount=0 -> destroy.
    Terminal states: no-op (but still force_cancel a node it orphaned, C9).

    ``deps`` is a SimpleNamespace with ``db_pool, controller, inventory,
    deploys, pool_repo, jobs_repo, event_bus``. Returns
    ``{"deployment_id", "status"}``; raises HTTPException(404) for an unknown
    deploy and HTTPException(502) when the destroy enqueue fails (behaviour
    identical to the pre-extraction ``/terminate`` route).
    """
    db_pool = deps.db_pool
    controller = deps.controller
    inventory = deps.inventory
    deploys = deps.deploys
    pool_repo = deps.pool_repo
    jobs_repo = deps.jobs_repo

    row = await deploys.get(deploy_uuid)
    if row is None:
        raise HTTPException(status_code=404, detail="deployment not found")

    state = row.get("state")
    target_node_id = row.get("target_node_id")
    gpu_per_replica = int(row.get("gpu_per_replica") or 0)
    pool_id = row.get("pool_id") or row.get("target_pool_id")
    org_id = row.get("org_id")

    if state in ("STOPPED", "TERMINATED", "FAILED"):
        # C9: a terminal deploy may still own a LIVE EC2 — e.g. it reached
        # FAILED after a successful pulumi up (bootstrap timeout). If no OTHER
        # live deploy targets its node, destroy the node so the instance does
        # not leak. Best-effort: never fail the (idempotent) terminate on it.
        if target_node_id is not None:
            try:
                async with db_pool.acquire() as _c:
                    _others = await _c.fetchval(
                        "SELECT count(*) FROM model_deployments "
                        "WHERE target_node_id=$1 AND deployment_id<>$2 "
                        "AND state IN ('PENDING_NODE','DEPLOYING','RUNNING','CREATED','PENDING')",
                        target_node_id, deploy_uuid,
                    )
                    _node_provider = await _c.fetchval(
                        "SELECT provider::text FROM compute_inventory WHERE id=$1",
                        target_node_id,
                    )
                if not _others and _node_provider:
                    await _initiate_node_destroy(
                        db_pool=db_pool, jobs_repo=jobs_repo,
                        node_id=target_node_id, pool_id=pool_id,
                        org_id=org_id, provider=_node_provider,
                    )
            except Exception:
                logger.warning("terminal-deploy node cleanup failed for %s",
                               target_node_id, exc_info=True)
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
        should_destroy = False
        async with db_pool.acquire() as conn:
            async with conn.transaction():
                # Atomically claim the TERMINATED transition. A concurrent
                # terminate of the SAME deploy (double-click / retry) would
                # otherwise both pass the early terminal-state check and both
                # release_gpu, under-counting gpu_allocated. Only the winner
                # of this guarded UPDATE releases the GPU.
                won = await deploys.update_state_if(
                    deploy_uuid, expected_state="PENDING_NODE",
                    new_state="TERMINATED", tx=conn,
                )
                if won:
                    await deploys.unbind(deploy_uuid, tx=conn)
                    if target_node_id is not None and gpu_per_replica > 0:
                        result = await inventory.release_gpu(
                            target_node_id, gpu_per_replica, tx=conn,
                        )
                        should_destroy = result.should_destroy
        if not won:
            # Lost the race — already terminated. Idempotent success.
            return {"deployment_id": str(deploy_uuid), "status": "TERMINATED"}
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
    if target_node_id is not None and controller is not None:
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

    should_destroy = False
    async with db_pool.acquire() as conn:
        async with conn.transaction():
            # Guard the GPU release behind an atomic TERMINATED claim so two
            # concurrent terminates of the same deploy can't both release
            # (which would under-count gpu_allocated). `state` is the value
            # read before unload_model; if it drifted/was already terminated,
            # the claim fails and we skip the release.
            won = await deploys.update_state_if(
                deploy_uuid, expected_state=state,
                new_state="TERMINATED", tx=conn,
            )
            if won:
                await deploys.unbind(deploy_uuid, tx=conn)
                if target_node_id is not None and gpu_per_replica > 0:
                    result = await inventory.release_gpu(
                        target_node_id, gpu_per_replica, tx=conn,
                    )
                    should_destroy = result.should_destroy
    if not won:
        return {"deployment_id": str(deploy_uuid), "status": "TERMINATED"}

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


@router.post("/terminate")
async def terminate_deployment(req: TerminateDeploymentRequest, request: Request):
    """REST entrypoint for refcount-aware deploy termination.

    Thin wrapper: parse the deployment_id, build ``deps`` from app.state, then
    delegate to ``terminate_deployment_core`` — the SAME teardown the gRPC
    delete path uses (via worker.handle_terminate_requested). Behaviour is
    identical to the pre-extraction route.
    """
    from uuid import UUID

    try:
        deploy_uuid = UUID(req.deployment_id)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="invalid deployment_id")

    deps = _build_terminate_deps(
        request.app.state.pool,
        controller=request.app.state.worker_controller,
        event_bus=getattr(request.app.state, "event_bus", None),
    )
    return await terminate_deployment_core(deploy_uuid, deps=deps)


def _resume_workload_type(row) -> str:
    """Resolve a deployment row's workload_type from its persisted
    ``configuration`` jsonb. Matches
    ``ModelDeploymentController._extract_workload_type`` for the ``external``
    decision so the HTTP resume path and the gRPC path agree on what counts as
    ``external`` — but is defensively guarded against a non-dict
    ``configuration`` (it checks ``isinstance(parsed, dict)`` and catches
    ``ValueError``/``TypeError``), whereas the controller would ``AttributeError``
    on a JSON config that decodes to a non-dict.

    Defaults to ``inference`` for older rows that never recorded one.
    """
    config = _source_field(row, "configuration")
    if isinstance(config, dict):
        wt = config.get("workload_type")
        return str(wt) if wt else "inference"
    if isinstance(config, str):
        try:
            parsed = json.loads(config)
        except (ValueError, TypeError):
            return "inference"
        wt = parsed.get("workload_type") if isinstance(parsed, dict) else None
        return str(wt) if wt else "inference"
    return "inference"


async def _start_deployment_impl(
    *,
    deployment_id: str,
    db_pool,
    controller,
    pool_repo,
    inventory,
    deploys,
    placer,
    jobs_repo,
) -> tuple[dict, int]:
    """Resume core: re-place a stopped/terminated/failed deployment onto a
    freshly provisioned node via the SAME ``place_and_provision`` path as
    ``/deploy``. For compute workloads this also clears the stale
    ``target_node_id`` left over from the paused run (the old node is gone);
    external workloads short-circuit to RUNNING before that point since they
    are never node-bound.

    This deliberately does NOT touch the legacy gRPC ``StartDeployment`` /
    ``model.deploy.requested`` worker (which calls ``adapter.provision_node``
    and raises ``NotImplementedError`` for AWS — sending every resumed AWS
    deploy to FAILED with no new EC2 provisioned).

    Extracted from the route so it can be unit-tested with mocked repos.
    Returns ``(response_body, response_status)`` and raises the same
    ``HTTPException`` as the inlined logic.
    """
    # 1. Parse + load the deployment row.
    try:
        deploy_uuid = UUID(deployment_id)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="invalid deployment_id")

    row = await deploys.get(deploy_uuid)
    if row is None:
        raise HTTPException(status_code=404, detail="deployment not found")

    state = _source_field(row, "state")
    if state not in ("STOPPED", "TERMINATED", "FAILED"):
        raise HTTPException(
            status_code=422,
            detail=f"cannot start deployment in state {state}",
        )

    org_id = _source_field(row, "org_id")

    # 2. External workloads need no compute — flip to RUNNING and return.
    # External deploys are never node-bound (the controller sends them straight
    # to RUNNING on create with no placement), so there's no stale binding to
    # clear here; we short-circuit before the unbind/reset block below.
    if _resume_workload_type(row) == "external":
        await deploys.set_state(deploy_uuid, "RUNNING")
        return {"deployment_id": str(deploy_uuid), "state": "RUNNING"}, 200

    # 3. Resolve + validate the target pool.
    pool_id = _source_field(row, "pool_id") or _source_field(row, "target_pool_id")
    if pool_id is None:
        raise HTTPException(status_code=404, detail="pool not found")
    if not isinstance(pool_id, UUID):
        try:
            pool_id = UUID(str(pool_id))
        except (TypeError, ValueError):
            raise HTTPException(status_code=404, detail="pool not found")

    pool_row = await pool_repo.get(pool_id)
    if pool_row is None:
        raise HTTPException(status_code=404, detail="pool not found")
    lifecycle = (pool_row.get("lifecycle_state") or "running").lower()
    if lifecycle in ("terminating", "terminated"):
        raise HTTPException(status_code=409, detail=f"pool is {lifecycle}")

    # Normalize pool metadata (jsonb may arrive as a str under asyncpg).
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

    # 4. Clear the stale binding + reset to CREATED so PoolPlacer /
    # create_placeholder run on a clean slate (the old target_node_id points at
    # a destroyed node).
    async with db_pool.acquire() as conn:
        async with conn.transaction():
            await deploys.unbind(deploy_uuid, tx=conn)
            await deploys.set_state(deploy_uuid, "CREATED", tx=conn)

    # 5. Re-run the shared place+provision core.
    # Read back the persisted ami_id from the deployment row's configuration so
    # the operator's AMI selection is honoured on resume instead of falling back
    # to resolve_ami's auto-pick.
    _resume_cfg = _source_field(row, "configuration")
    if isinstance(_resume_cfg, str):
        try:
            _resume_cfg = json.loads(_resume_cfg)
        except (ValueError, TypeError):
            _resume_cfg = {}
    _resume_ami = _resume_cfg.get("ami_id") if isinstance(_resume_cfg, dict) else None

    deps = SimpleNamespace(
        db_pool=db_pool,
        controller=controller,
        inventory=inventory,
        deploys=deploys,
        placer=placer,
        jobs_repo=jobs_repo,
    )
    body, status = await place_and_provision(
        deploy_id=deploy_uuid,
        pool_id=pool_id,
        pool_row=pool_row,
        pool_meta=pool_meta,
        gpu_per_replica=int(_source_field(row, "gpu_per_replica") or 0),
        org_id=org_id,
        engine=_source_field(row, "engine"),
        load_spec_source=row,
        deps=deps,
        ami_id=_resume_ami,
    )
    return body, status


@router.post("/start")
async def start_deployment(req: TerminateDeploymentRequest, request: Request):
    """Resume (redeploy) a stopped/terminated/failed deployment.

    Canonical resume: runs the SAME synchronous place+provision path as
    ``/deploy`` (``place_and_provision``) so a fresh node is provisioned and
    the model is (re)loaded. The stale ``target_node_id`` left over from the
    paused run is cleared first. This route — NOT the legacy gRPC
    ``StartDeployment`` / ``model.deploy.requested`` worker — is the supported
    resume entry point.
    """
    from orchestration.models.model_deployment.pool_placer import (
        PoolPlacer,
    )
    from orchestration.repositories.inventory_repo import (
        InventoryRepository,
    )
    from orchestration.repositories.model_deployment_repo import (
        ModelDeploymentRepository,
    )
    from orchestration.repositories.pool_repo import (
        ComputePoolRepository,
    )
    from orchestration.state_machine.jobs.repository import (
        ProvisioningJobRepository,
    )
    from fastapi.responses import JSONResponse

    db_pool = request.app.state.pool
    controller = request.app.state.worker_controller
    event_bus = getattr(request.app.state, "event_bus", None)

    pool_repo = ComputePoolRepository(db_pool)
    inventory = InventoryRepository(db_pool)
    deploys = ModelDeploymentRepository(db_pool, event_bus=event_bus)
    placer = PoolPlacer(db_pool)
    jobs_repo = ProvisioningJobRepository(db_pool)

    body, status = await _start_deployment_impl(
        deployment_id=req.deployment_id,
        db_pool=db_pool,
        controller=controller,
        pool_repo=pool_repo,
        inventory=inventory,
        deploys=deploys,
        placer=placer,
        jobs_repo=jobs_repo,
    )

    # PoolAtCapacity surfaces as a 503 POOL_AT_CAPACITY body — mirror /deploy:
    # skip the success audit and return with a Retry-After hint.
    if status == 503 and body.get("error") == "POOL_AT_CAPACITY":
        return JSONResponse(
            status_code=status, content=body,
            headers={"Retry-After": "60"},
        )

    await log_audit_event(
        user_id=None,
        action="deployment.start",
        resource_type="deployment",
        resource_id=req.deployment_id,
        status="success",
        org_id=await _lookup_org_id("deployment", req.deployment_id),
    )

    return JSONResponse(status_code=status, content=body)


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
                "SELECT state, target_node_id FROM model_deployments "
                "WHERE deployment_id = $1", dep_uuid
            )

            if not row:
                raise HTTPException(status_code=404, detail="Deployment not found")

            # Allow deletion of terminal deployments, plus the pre-binding
            # CREATED/PENDING states (no node bound, no GPU allocated, nothing
            # loaded on a worker — safe to drop directly without a terminate
            # round-trip). Anything else (PENDING_NODE/DEPLOYING/RUNNING) holds
            # resources and must be terminated first.
            _deletable = row["state"] in ("STOPPED", "TERMINATED", "FAILED")
            if not _deletable and row["state"] in ("CREATED", "PENDING") \
                    and row["target_node_id"] is None:
                _deletable = True
            if not _deletable:
                raise HTTPException(
                    status_code=400,
                    detail=f"Cannot delete deployment in state '{row['state']}'. Stop it first.",
                )

            # Node teardown BEFORE the row drop. A terminal deploy
            # (FAILED/STOPPED) may still OWN a live EC2 node — e.g. it reached
            # FAILED after a successful pulumi up / bootstrap timeout. Dropping
            # the model_deployments row without tearing the node down leaks the
            # instance. If no OTHER non-terminal deploy targets the node, route
            # it through the SAME force_cancel -> reconciler CancelHandler ->
            # node-scoped `pulumi destroy` path that /terminate's C9 branch
            # uses. Best-effort: an idempotent delete must never fail on the
            # teardown (mirrors terminate_deployment_core's C9 block).
            _target_node_id = row["target_node_id"]
            if _target_node_id is not None:
                try:
                    _others = await conn.fetchval(
                        "SELECT count(*) FROM model_deployments "
                        "WHERE target_node_id=$1 AND deployment_id<>$2 "
                        "AND state IN ('PENDING_NODE','DEPLOYING','RUNNING','CREATED','PENDING')",
                        _target_node_id, dep_uuid,
                    )
                    _node_provider = await conn.fetchval(
                        "SELECT provider::text FROM compute_inventory WHERE id=$1",
                        _target_node_id,
                    )
                    if not _others and _node_provider:
                        from orchestration.state_machine.jobs.repository import (
                            ProvisioningJobRepository,
                        )
                        _node_pool_id = await conn.fetchval(
                            "SELECT pool_id FROM compute_inventory WHERE id=$1",
                            _target_node_id,
                        )
                        _teardown_pool = await asyncpg.create_pool(
                            POSTGRES_DSN, min_size=1, max_size=1,
                        )
                        try:
                            await _initiate_node_destroy(
                                db_pool=_teardown_pool,
                                jobs_repo=ProvisioningJobRepository(_teardown_pool),
                                node_id=_target_node_id,
                                pool_id=_node_pool_id,
                                org_id=audit_org_id,
                                provider=_node_provider,
                            )
                        finally:
                            await _teardown_pool.close()
                except Exception:
                    logger.warning(
                        "delete: node teardown failed for node=%s (deploy=%s); "
                        "dropping the row anyway",
                        _target_node_id, dep_uuid, exc_info=True,
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
                # Explicit so the two HTTP delete paths are internally
                # consistent (the FK is ON DELETE CASCADE, but the /pool delete
                # path drops it explicitly — match that here too).
                await conn.execute(
                    "DELETE FROM deployment_terminal_logs WHERE deployment_id = $1",
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
    from orchestration.repositories.pool_repo import (
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
        from providers.aws.pool_metadata import (
            AWSPoolMetadata,
        )
        from pydantic import ValidationError as _ValidationError
        try:
            AWSPoolMetadata(**req.metadata)
        except _ValidationError as e:
            raise HTTPException(status_code=422, detail={"errors": e.errors()})

    # 2b. Require region_constraint for AWS pools. Since the account-wide AWS
    #     region was removed (Task 3), region MUST come from the pool. Accepting
    #     a region-less AWS pool would let it through to deploy time where it
    #     fails with an opaque internal error instead of a clear early rejection.
    if req.provider == "aws" and not req.region_constraint:
        raise HTTPException(
            status_code=422,
            detail="region_constraint is required for AWS pools",
        )

    # Validate AWS region(s) at the boundary. A malformed code (e.g.
    #     "us-east1" missing the second hyphen) is otherwise persisted and only
    #     fails much later at preflight with an opaque
    #     "DLAMI lookup failed: EndpointConnectionError" (boto3 can't build an
    #     endpoint for a nonexistent region). Reject early with a clear message.
    if req.provider == "aws" and req.region_constraint:
        from providers.aws.region import (
            InvalidRegionError,
            validate_aws_region,
        )
        for _region in req.region_constraint:
            try:
                validate_aws_region(_region)
            except InvalidRegionError as e:
                raise HTTPException(status_code=422, detail=str(e))

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
    if req.region_constraint:
        # Persist the region so deploy-time provisioning can read it. Without
        # this the AWS provisioning spec has no region and preflight fails
        # with "spec is missing required field: region".
        pool_data["region_constraint"] = list(req.region_constraint)
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
        from providers.aws.pool_metadata import (
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


@router.delete("/pool/{pool_id}")
async def delete_pool_rest(pool_id: str):
    """Delete a pool from the dashboard in one call: tear down every node's
    EC2 (via the provisioning reconciler) and soft-delete the pool row.

    This is the route ``poolService.deletePool()`` (DELETE
    /api/v1/deployment/pool/{id}) actually calls — previously there was no
    such handler, so the dashboard's "Delete Pool" button 404'd. It
    supersedes the two-step POST /stoppool + POST /deletepool flow:

      1. 404 if the pool is gone.
      2. cascade-DELETE EVERY deployment in the pool (the pool is going away,
         so its deployments go with it — no zombie rows pointing at torn-down
         nodes). Dependent rows that lack ON DELETE behavior (policies,
         api_keys, inference_logs) are detached/removed first, mirroring the
         single-deployment DELETE /deployment/delete/{id} cleanup.
      3. force-cancel EVERY provisioning job in the pool so the reconciler's
         CancelHandler destroys each ``inferia-<node_id>`` stack with the
         matching local backend, and flips each node's row to terminating.
         (The old pool-scoped direct-adapter destroy targeted a stack that
         never existed and silently leaked the EC2s.)
      4. soft-delete the pool row to the NON-FINAL ``lifecycle_state=
         'terminating'`` (is_active=FALSE). It is deliberately NOT set to
         'terminated' here: the EC2 destroys are asynchronous, so the pool row
         must outlive the delete request. The reconciler's PHASE-2 finalizer
         (``_teardown_node`` → ``finalize_pool_delete``) HARD-deletes the
         ``compute_pools`` row + every pool-scoped DB row once the LAST node
         has actually been purged — so a deleted pool eventually leaves ZERO
         residue instead of a permanent soft-deleted shell.

    All of 2-4 run in one DB transaction so a crash can't half-delete the pool.

    Returns 202 — node teardown is asynchronous (~60-90s per EC2); the
    reconciler purges each node as it finishes and finalizes the pool once the
    last node is gone.
    """
    try:
        pool_uuid = UUID(pool_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid pool_id")

    conn = None
    try:
        conn = await asyncpg.connect(POSTGRES_DSN)
        pool = await conn.fetchrow(
            "SELECT id, lifecycle_state, is_active FROM compute_pools "
            "WHERE id = $1 AND is_active = TRUE",
            pool_uuid,
        )
        if not pool:
            raise HTTPException(status_code=404, detail="Pool not found")

        # Cascade everything in one transaction so a crash can't leave the
        # pool half-deleted (e.g. jobs cancelled but the pool row still
        # active, or deployments orphaned onto torn-down nodes).
        async with conn.transaction():
            # 1. DELETE EVERY deployment in the pool. The pool is going away,
            #    so its deployments go with it (the EC2 nodes are torn down in
            #    step 2, reclaiming any GPUs — no per-node refcount needed).
            #    Detach/remove dependent rows first because these FKs were
            #    created without ON DELETE behavior (same cleanup as the
            #    single-deployment DELETE /deployment/delete/{id} path).
            await conn.execute(
                "UPDATE policies SET deployment_id = NULL "
                "WHERE deployment_id IN "
                "(SELECT deployment_id FROM model_deployments WHERE pool_id = $1)",
                pool_uuid,
            )
            await conn.execute(
                "UPDATE api_keys SET deployment_id = NULL "
                "WHERE deployment_id IN "
                "(SELECT deployment_id FROM model_deployments WHERE pool_id = $1)",
                pool_uuid,
            )
            await conn.execute(
                "DELETE FROM inference_logs "
                "WHERE deployment_id IN "
                "(SELECT deployment_id FROM model_deployments WHERE pool_id = $1)",
                pool_uuid,
            )
            await conn.execute(
                "DELETE FROM model_deployments WHERE pool_id = $1",
                pool_uuid,
            )
            # 2. Tear down every node's EC2 via the reconciler (see docstring):
            #    flip live jobs to 'cancelling' so the CancelHandler destroys
            #    each inferia-<node_id> stack.
            await conn.execute(
                """
                UPDATE provisioning_jobs
                SET phase = 'cancelling',
                    next_attempt_after = NULL,
                    lease_holder = NULL,
                    lease_expires_at = NULL,
                    updated_at = now()
                WHERE pool_id = $1
                  AND phase NOT IN ('cancelling', 'terminated')
                """,
                pool_uuid,
            )
            # 3. Flag each node row terminating so the dashboard shows the
            #    teardown spinner immediately.
            await conn.execute(
                """
                UPDATE compute_inventory
                SET metadata = COALESCE(metadata, '{}'::jsonb)
                               || jsonb_build_object('terminating', true),
                    updated_at = now()
                WHERE pool_id = $1 AND state IS DISTINCT FROM 'terminated'
                """,
                pool_uuid,
            )
            # 4. Soft-delete the pool row to the NON-FINAL 'terminating'
            #    state. NOT 'terminated': the EC2 destroys are async, so the
            #    pool row must outlive this request. The reconciler's PHASE-2
            #    finalizer hard-deletes compute_pools + every pool-scoped row
            #    once the LAST node is purged (zero residue). Keying the
            #    finalizer trigger off 'terminating' (vs the final
            #    'terminated') is what lets it distinguish "delete in flight"
            #    from "delete already finished".
            await conn.execute(
                """
                UPDATE compute_pools
                SET lifecycle_state = $2, is_active = FALSE, updated_at = now()
                WHERE id = $1
                """,
                pool_uuid, POOL_STATE_TERMINATING,
            )

        # 5. Empty / already-drained pool: finalize NOW. The reconciler's
        #    PHASE-2 finalizer (``_teardown_node`` → ``finalize_pool_delete``)
        #    only fires off a per-node teardown EVENT — and a pool with ZERO
        #    live nodes (one whose nodes already failed/never provisioned, or
        #    whose nodes were purged by a prior stop) produces no such event,
        #    so that pool would be stuck 'terminating' forever (DB residue: the
        #    pool row + the UNIQUE(pool_name, owner_type, owner_id) never
        #    freed). Step 2 already DELETEd the pool's deployments and step 3
        #    flagged any nodes terminating, so check whether any
        #    compute_inventory rows remain; if none, run the finalizer right
        #    here. Best-effort: a failure leaves the pool soft-deleted and a
        #    later node teardown still finalizes it (finalize is idempotent, so
        #    a double-fire is a harmless no-op). No EC2 sweep here — there are
        #    no live nodes (and the per-node sweeps already ran for any that
        #    were torn down).
        try:
            remaining = await conn.fetchval(
                "SELECT count(*) FROM compute_inventory WHERE pool_id = $1",
                pool_uuid,
            )
            if int(remaining or 0) == 0:
                from orchestration.repositories.pool_repo import (
                    ComputePoolRepository,
                )
                async with conn.transaction():
                    deleted = await ComputePoolRepository(None).finalize_pool_delete(
                        pool_uuid, tx=conn,
                    )
                if deleted:
                    logger.info(
                        "delete_pool_rest: pool %s had zero live nodes; "
                        "finalized (hard-deleted) immediately", pool_uuid,
                    )
        except Exception as e:
            logger.warning(
                "delete_pool_rest: empty-pool finalize for %s failed "
                "(best-effort; a later node teardown will finalize): %s",
                pool_uuid, e,
            )
    finally:
        if conn:
            await conn.close()

    await log_audit_event(
        user_id=None,
        action="pool.delete",
        resource_type="compute_pool",
        resource_id=pool_id,
        status="success",
        org_id=await _lookup_org_id("compute_pool", pool_id),
    )

    from fastapi.responses import JSONResponse
    return JSONResponse(
        status_code=202,
        content={"pool_id": pool_id, "status": "TERMINATING"},
    )


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
            SELECT node_ids, target_node_id
            FROM model_deployments
            WHERE deployment_id = $1
            """,
            dep_uuid,
        )

        if not dep_nodes:
            return {"logs": ["Waiting for node provisioning..."]}

        # node_ids is set only at RUNNING; during DEPLOYING fall back to the
        # bound target_node_id so pull/lifecycle logs are reachable.
        node_id = (
            dep_nodes["node_ids"][0]
            if dep_nodes["node_ids"]
            else dep_nodes["target_node_id"]
        )
        if not node_id:
            return {"logs": ["Waiting for node provisioning..."]}

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
    from orchestration.repositories.terminal_log_repo import (
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
            SELECT p.provider, p.provider_credential_name, d.node_ids,
                   d.target_node_id, d.org_id
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
        # During DEPLOYING the deployment is bound (target_node_id) but node_ids
        # is only populated at RUNNING. Fall back to target_node_id so the
        # dashboard can stream image-pull / lifecycle logs while the model
        # container is still being pulled.
        node_id = dep["node_ids"][0] if dep["node_ids"] else dep["target_node_id"]
        if not node_id:
            return {"error": "No nodes assigned to this deployment yet."}

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
            # The real model slug (e.g. hf://gemma3:4b), distinct from the
            # human deployment name in model_name. The dashboard's Model column
            # binds to this; without it the column falls back to model_name and
            # shows the deployment name twice.
            "inference_model": d.inference_model,
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
    from orchestration.provisioning.engine.registry import (
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

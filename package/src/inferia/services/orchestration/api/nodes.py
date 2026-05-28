"""
/v1/nodes/* — the node-centric API surface.

The pool concept is hidden from this layer; every node implicitly lives in
the caller's organization's __default__ pool (resolved lazily via
ComputePoolRepository.ensure_default_pool).
"""

from __future__ import annotations

import logging
import os
import time
import uuid
from typing import Any, Callable

from fastapi import (
    APIRouter,
    Depends,
    Header,
    HTTPException,
    Path,
    Query,
    Request,
    Response,
    status,
)
from pydantic import BaseModel, Field, field_validator

from inferia.services.orchestration.repositories.inventory_repo import (
    LabelConflictError,
    NodeNotFoundError,
    NodeTerminatedError,
)

logger = logging.getLogger("inferia.nodes_api")
router = APIRouter(prefix="/v1/nodes")


# ---------------------------------------------------------------------------
# DI.
# ---------------------------------------------------------------------------


class _Deps:
    inventory_repo: Any = None
    pool_repo: Any = None
    worker_auth: Any = None
    control_plane_external_url: str = ""
    adapters: dict[str, Any] = {}
    require_permission: Callable[[str], Any] | None = None
    provisioning_repo: Any = None
    db_pool: Any = None


_deps = _Deps()


def configure(
    *,
    inventory_repo,
    pool_repo,
    worker_auth,
    control_plane_external_url: str,
    adapters: dict[str, Any],
    require_permission,
    provisioning_repo=None,
    db_pool=None,
) -> None:
    _deps.inventory_repo = inventory_repo
    _deps.pool_repo = pool_repo
    _deps.worker_auth = worker_auth
    _deps.control_plane_external_url = control_plane_external_url
    _deps.adapters = dict(adapters)
    _deps.require_permission = require_permission
    _deps.provisioning_repo = provisioning_repo
    _deps.db_pool = db_pool


def _need_perm(perm: str):
    async def _dep(authorization: str | None = Header(default=None)):
        if _deps.require_permission is None:
            raise HTTPException(503, "RBAC dependency not configured")
        check = _deps.require_permission(perm)
        try:
            result = check(authorization)
        except TypeError:
            result = check()
        if hasattr(result, "__await__"):
            result = await result
        return result
    return _dep


def _org_id_from_headers(authorization, x_organization_id) -> str:
    if not x_organization_id:
        raise HTTPException(401, "missing X-Organization-ID header")
    return x_organization_id


# ---------------------------------------------------------------------------
# Pydantic schemas.
# ---------------------------------------------------------------------------


_MAX_LABELS = 32
_MAX_KEY_LEN = 253
_MAX_VAL_LEN = 253


def _validate_label_map(value: dict[str, str]) -> dict[str, str]:
    if len(value) > _MAX_LABELS:
        raise ValueError(f"labels payload exceeds {_MAX_LABELS} entries")
    for k, v in value.items():
        if not isinstance(k, str) or not k or len(k) > _MAX_KEY_LEN:
            raise ValueError(f"label key length must be 1..{_MAX_KEY_LEN}: {k!r}")
        if not isinstance(v, str) or len(v) > _MAX_VAL_LEN:
            raise ValueError(f"label value length must be 0..{_MAX_VAL_LEN}: {v!r}")
        if any(ord(c) < 0x20 for c in k) or any(ord(c) < 0x20 for c in v):
            raise ValueError("label keys/values must not contain control characters")
    return value


class NodeView(BaseModel):
    id: str
    pool_id: str | None = None
    node_name: str | None = None
    agent_kind: str | None = None
    provider: str | None = None
    state: str
    labels: dict[str, str] = Field(default_factory=dict)
    advertise_url: str | None = None
    expose_url: str | None = None
    gpu_total: int | None = None
    gpu_allocated: int | None = None
    vcpu_total: int | None = None
    vcpu_allocated: int | None = None
    ram_gb_total: int | None = None
    ram_gb_allocated: int | None = None
    last_heartbeat: str | None = None
    provider_instance_id: str | None = None


class ListResponse(BaseModel):
    nodes: list[NodeView]


class PatchLabelsBody(BaseModel):
    add: dict[str, str] = Field(default_factory=dict)
    remove: list[str] = Field(default_factory=list)

    @field_validator("add")
    @classmethod
    def _add_ok(cls, v):
        return _validate_label_map(v)


class AddWorkerBody(BaseModel):
    node_name: str = Field(min_length=1, max_length=255)
    advertise_url: str | None = None
    labels: dict[str, str] = Field(default_factory=dict)

    @field_validator("labels")
    @classmethod
    def _labels_ok(cls, v):
        return _validate_label_map(v)


class AddWorkerResponse(BaseModel):
    node_id: str
    bootstrap_token: str
    expires_at: int
    control_plane_url: str
    inference_token: str
    env_snippet: str


class AddProviderNodeBody(BaseModel):
    """Generic shape used for Nosana / Akash / other DePIN add-node calls.

    The adapter-specific keys (gpu_type, market_address, etc.) pass through
    in ``spec`` and are interpreted by the per-provider provision_single_node.
    """

    node_name: str | None = None
    labels: dict[str, str] = Field(default_factory=dict)
    spec: dict[str, Any] = Field(default_factory=dict)
    # Most current adapters require a credential_name; we don't enforce here
    # so unknown providers can supply their own field set in `spec`.
    credential_name: str | None = None

    @field_validator("labels")
    @classmethod
    def _labels_ok(cls, v):
        return _validate_label_map(v)


class AddProviderNodeResponse(BaseModel):
    node_id: str
    provider: str
    provider_instance_id: str | None = None
    state: str
    # Present only for providers that use the async provisioning queue
    # (aws today). Other providers (nosana, akash) still use the
    # legacy fire-and-forget adapter path and return job_id=None.
    job_id: str | None = None


class ProvisioningPhase(BaseModel):
    phase: str
    status: str
    started_at: str | None = None
    ended_at: str | None = None
    last_message: str | None = None


class ProvisioningSummary(BaseModel):
    current_phase: str | None = None
    terminal: bool
    phases: list[ProvisioningPhase]
    # The UI consumes these on the Overview tab: error → red banner with hint
    # + Retry button, aws_metadata → metadata grid, attempt_count → "Retry N"
    # subtitle. job_id surfaced so the UI can call POST .../provisioning/retry
    # without an extra round-trip.
    #
    # error is a free-form dict (not a sub-model) because the contract uses
    # the key "class" — a reserved Python word that's awkward as a Pydantic
    # field name. The shape is locked to {code, message, hint, class}.
    attempt_count: int = 0
    error: dict[str, Any] | None = None
    aws_metadata: dict[str, Any] | None = None
    job_id: str | None = None


class ProvisioningEvent(BaseModel):
    id: int
    phase: str
    status: str
    message: str | None = None
    created_at: str


class ProvisioningLogsResponse(BaseModel):
    events: list[ProvisioningEvent]
    next_after: int | None = None


class EC2ConsoleResponse(BaseModel):
    logs: list[str]
    fetched_at: str


# ---------------------------------------------------------------------------
# Routes.
# ---------------------------------------------------------------------------


def _parse_selector(raw: str | None) -> dict[str, str] | None:
    if not raw:
        return None
    out: dict[str, str] = {}
    for chunk in raw.split(","):
        if "=" not in chunk:
            raise HTTPException(422, "selector expected key=value pairs")
        k, v = chunk.split("=", 1)
        k, v = k.strip(), v.strip()
        if not k:
            raise HTTPException(422, "selector key must be non-empty")
        out[k] = v
    return out


@router.get("", response_model=ListResponse, include_in_schema=False)
@router.get("/", response_model=ListResponse)
async def list_nodes(
    labels: str | None = Query(default=None, description="key=value,key=value (AND)"),
    x_organization_id: str | None = Header(default=None, alias="X-Organization-ID"),
    authorization: str | None = Header(default=None),
    _granted: bool = Depends(_need_perm("deployment:list")),
):
    org_id = _org_id_from_headers(authorization, x_organization_id)
    selector = _parse_selector(labels)
    rows = await _deps.inventory_repo.list_nodes(org_id=org_id, selector=selector)
    return ListResponse(nodes=[_to_view(r) for r in rows])


@router.get("/{node_id}", response_model=NodeView)
async def get_node(
    node_id: str = Path(...),
    _granted: bool = Depends(_need_perm("deployment:list")),
):
    row = await _deps.inventory_repo.get_node(node_id=node_id)
    if not row:
        raise HTTPException(404, "node not found")
    return _to_view(row)


@router.patch("/{node_id}/labels", response_model=NodeView)
async def patch_labels(
    body: PatchLabelsBody,
    node_id: str = Path(...),
    _granted: bool = Depends(_need_perm("deployment:update")),
):
    overlap = set(body.add.keys()) & set(body.remove)
    if overlap:
        raise HTTPException(422, f"keys in both add and remove: {sorted(overlap)}")
    try:
        row = await _deps.inventory_repo.set_labels(
            node_id=node_id, add=body.add, remove=body.remove,
        )
    except NodeNotFoundError:
        raise HTTPException(404, "node not found")
    except NodeTerminatedError:
        raise HTTPException(409, "node is terminated")
    except LabelConflictError as e:
        raise HTTPException(422, str(e))
    except ValueError as e:
        raise HTTPException(422, str(e))
    return _to_view(row)


@router.delete("/{node_id}")
async def delete_node(
    node_id: str = Path(...),
    _granted: bool = Depends(_need_perm("deployment:delete")),
):
    existing = await _deps.inventory_repo.get_node(node_id=node_id)
    if not existing:
        raise HTTPException(404, "node not found")
    provider = existing.get("provider")
    pool_id = existing.get("pool_id")

    # T26: if a non-terminal provisioning job exists for this node, mark
    # it for cancellation and return 204. The reconciler's CancelHandler
    # owns the actual teardown (pulumi destroy + state machine progress).
    # This path supersedes the legacy AWS destroy spawn below because the
    # state machine is the new authoritative driver for terminating
    # AWS nodes that went through the provisioning_jobs queue.
    try:
        nuuid = uuid.UUID(str(node_id))
    except (ValueError, TypeError):
        nuuid = None
    if _deps.provisioning_repo is not None and nuuid is not None and hasattr(
        _deps.provisioning_repo, "get_by_node",
    ):
        job = await _deps.provisioning_repo.get_by_node(node_id=nuuid)
        if job is not None and not job.phase.is_terminal:
            await _deps.provisioning_repo.request_cancel(node_id=nuuid)
            return Response(status_code=status.HTTP_204_NO_CONTENT)
        # Terminal job → fall through to the idempotent soft-delete path
        # below. We do NOT re-spawn pulumi destroy for terminal jobs;
        # the CancelHandler already ran (or the job failed terminally
        # without ever spinning up infra, in which case there is nothing
        # to destroy).
        if job is not None and job.phase.is_terminal:
            await _deps.inventory_repo.set_state(
                node_id=node_id, state="terminated",
            )
            return Response(status_code=status.HTTP_204_NO_CONTENT)

    # AWS nodes must destroy the underlying EC2 stack before the row
    # disappears from inventory. Non-AWS providers (worker/on_prem,
    # nosana, akash, gcp/azure for now) keep the original soft-delete
    # 204 behaviour.
    if provider == "aws" and _deps.db_pool is not None and pool_id:
        from inferia.services.orchestration.services.adapter_engine import (
            aws_deprovision,
        )
        # Synchronous flip to terminating so the dashboard sees the
        # state transition immediately. mark_terminating_node falls
        # back to soft_delete_node when the inventory repo predates
        # the new state — defensive against partial deploys.
        if hasattr(_deps.inventory_repo, "mark_terminating_node"):
            await _deps.inventory_repo.mark_terminating_node(node_id=node_id)
        aws_deprovision._spawn_destroy(
            pool_id=str(pool_id),
            node_id=str(node_id),
            db_pool=_deps.db_pool,
        )
        return Response(
            content=__import__("json").dumps(
                {"node_id": str(node_id), "state": "terminating"},
            ),
            media_type="application/json",
            status_code=status.HTTP_202_ACCEPTED,
        )

    # Non-AWS path (or AWS with no db_pool / pool_id available).
    if provider == "aws":
        logger.warning(
            "AWS node %s deleted without destroy: db_pool=%s pool_id=%s",
            node_id, _deps.db_pool is not None, pool_id,
        )
    await _deps.inventory_repo.soft_delete_node(node_id=node_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{node_id}/provisioning/retry")
async def retry_provisioning(
    node_id: str = Path(...),
    _granted: bool = Depends(_need_perm("deployment:create")),
):
    """Re-enqueue a failed provisioning job.

    Resets the job row to phase='pending', attempt_count=0, and clears
    all error fields so the reconciler picks it up on the next claim
    tick. Inventory state transitions failed → provisioning so the
    dashboard reflects the requeue immediately.

    Returns 409 if the job is not in 'failed' state (e.g. the user
    races a still-running job by clicking Retry while it's already
    re-trying on its own), 404 if the node row is missing, or 503 if
    the provisioning queue is not configured.
    """
    row = await _deps.inventory_repo.get_node(node_id=node_id)
    if not row:
        raise HTTPException(404, "node not found")
    if _deps.provisioning_repo is None:
        raise HTTPException(503, "provisioning queue not configured")
    try:
        nuuid = uuid.UUID(str(node_id))
    except (ValueError, TypeError):
        raise HTTPException(400, "node_id is not a valid uuid")
    job = await _deps.provisioning_repo.reset_for_retry(node_id=nuuid)
    if job is None:
        raise HTTPException(409, "no failed job to retry")
    # Reset inventory state too (failed → provisioning) so the UI's
    # Overview pane stops showing the failed banner the instant the
    # retry POST returns.
    await _deps.inventory_repo.set_state(
        node_id=node_id, state="provisioning",
    )
    return {"job_id": str(job.id), "phase": job.phase.value}


# ---------------------------------------------------------------------------
# add/{provider}
# ---------------------------------------------------------------------------


@router.post("/add/worker", response_model=AddWorkerResponse)
async def add_worker_node(
    body: AddWorkerBody,
    request: Request,
    x_organization_id: str | None = Header(default=None, alias="X-Organization-ID"),
    authorization: str | None = Header(default=None),
    _granted: bool = Depends(_need_perm("deployment:create")),
):
    org_id = _org_id_from_headers(authorization, x_organization_id)
    # asyncpg returns native UUID objects from uuid columns; cast to str
    # before passing to JWT mint (json.dumps can't serialise UUID).
    pool_id = str(await _deps.pool_repo.ensure_default_pool(org_id=org_id))
    inference_token = ""
    # Workers need an inference_token to authenticate inbound CP→worker traffic.
    if hasattr(_deps.pool_repo, "get_or_generate_inference_token"):
        inference_token = await _deps.pool_repo.get_or_generate_inference_token(
            pool_id=pool_id,
        ) or ""
    node = await _deps.inventory_repo.upsert_worker(
        pool_id=pool_id,
        node_name=body.node_name,
        advertise_url=body.advertise_url or "",
        allocatable={},
    )
    # Apply labels if supplied.
    if body.labels:
        try:
            await _deps.inventory_repo.set_labels(
                node_id=node["id"], add=body.labels, remove=[],
            )
        except Exception as e:
            logger.warning("failed to apply labels to %s: %s", node["id"], e)
    bootstrap_token = _deps.worker_auth.mint_bootstrap_token(
        pool_id=pool_id, ttl_seconds=3600,
    )
    expires_at = int(time.time()) + 3600
    # Resolve the URL the worker should use to reach the control plane. Prefer
    # the operator-configured CONTROL_PLANE_EXTERNAL_URL. If unset, default
    # to the Docker service hostname 'inferia-app' (the worker compose
    # attaches to the same network, so this resolves inside the worker
    # container). The request-host fallback is last-resort because it's
    # often 127.0.0.1 inside the orchestration container, which is wrong
    # from a sibling worker's perspective.
    control_plane_url = (
        _deps.control_plane_external_url
        or os.getenv("WORKER_DEFAULT_CONTROL_PLANE_URL")
        or "http://inferia-app:8000"
    )
    # advertise_url default: the worker's compose service hostname on the
    # shared deploy_inferia-net network. http://localhost:8080 is NEVER
    # right for the same-host sibling-compose case — `localhost` inside the
    # control-plane container resolves to itself, not the worker. Operators
    # running the worker on a different host MUST pass body.advertise_url.
    advertise_url = body.advertise_url or "http://inferia-worker:8080"
    # IMPORTANT: docker-compose treats the entire characters after '=' as the
    # value (no inline comment stripping). Keep comments on their own lines.
    env_snippet = (
        "# Generated by InferiaLLM. Bootstrap token expires in 1h.\n"
        "# CONTROL_PLANE_URL: change to the URL this worker host can reach the\n"
        "# InferiaLLM control plane at. The default below works when the\n"
        "# worker compose runs on the same docker host (the worker container\n"
        "# attaches to deploy_inferia-net and resolves the service name).\n"
        f"CONTROL_PLANE_URL={control_plane_url}\n"
        f"BOOTSTRAP_TOKEN={bootstrap_token}\n"
        f"POOL_ID={pool_id}\n"
        f"NODE_NAME={body.node_name}\n"
        "# WORKER_ADVERTISE_URL: URL the control plane will use to reach this\n"
        "# worker's inference port. The default below works when the worker\n"
        "# compose runs as a sibling service on the same docker host.\n"
        "# Operators on a different host must replace it with a routable\n"
        "# URL (public IP, DNS, or the private address the control plane\n"
        "# can dial). Do NOT use http://localhost:8080 in any compose-on-\n"
        "# same-host setup — localhost inside the control-plane container\n"
        "# resolves to itself, not the worker.\n"
        f"WORKER_ADVERTISE_URL={advertise_url}\n"
        f"INFERENCE_TOKEN={inference_token}\n"
    )
    return AddWorkerResponse(
        node_id=str(node["id"]),
        bootstrap_token=bootstrap_token,
        expires_at=expires_at,
        control_plane_url=control_plane_url,
        inference_token=inference_token,
        env_snippet=env_snippet,
    )


@router.post("/add/{provider}", response_model=AddProviderNodeResponse)
async def add_provider_node(
    provider: str = Path(..., description="nosana | akash | etc."),
    body: AddProviderNodeBody = ...,
    x_organization_id: str | None = Header(default=None, alias="X-Organization-ID"),
    authorization: str | None = Header(default=None),
    _granted: bool = Depends(_need_perm("deployment:create")),
):
    if provider == "worker":
        # The dedicated /add/worker endpoint above handles this.
        raise HTTPException(404, "use POST /v1/nodes/add/worker for worker nodes")

    # ------------------------------------------------------------------
    # AWS path: thin enqueue. Validate the spec, create a 'provisioning'
    # placeholder row, enqueue a provisioning_jobs row. Return
    # (node_id, job_id) in well under one second. The reconciler does
    # the actual Pulumi up + bootstrap work asynchronously.
    # ------------------------------------------------------------------
    if provider == "aws":
        spec = dict(body.spec or {})
        instance_class = spec.get("instance_class")
        instance_type = spec.get("instance_type")
        region = spec.get("region")
        missing = [
            name for name, val in (
                ("instance_class", instance_class),
                ("instance_type", instance_type),
                ("region", region),
            )
            if not val
        ]
        if missing:
            raise HTTPException(
                422,
                f"aws spec missing required fields: {', '.join(missing)}",
            )

        # Catalog lookup. Reject types that the wizard never offered, and
        # reject class/type mismatches so the catalogue stays the single
        # source of truth.
        from inferia.services.orchestration.services.adapter_engine.adapters.aws import (
            instance_catalog,
        )
        it = instance_catalog.lookup(instance_type)
        if it is None:
            raise HTTPException(
                422,
                f"unknown instance_type: {instance_type!r} (not in catalog)",
            )
        if it.cls != instance_class:
            raise HTTPException(
                422,
                f"instance_class mismatch: spec says {instance_class!r} but "
                f"{instance_type!r} is class {it.cls!r}",
            )

        if _deps.provisioning_repo is None:
            raise HTTPException(503, "provisioning queue not configured")

        org_id = _org_id_from_headers(authorization, x_organization_id)
        pool_id = await _deps.pool_repo.ensure_default_pool(org_id=org_id)
        # Fold convenience fields into spec so adapters/reconciler see
        # them uniformly.
        if body.node_name is not None:
            spec.setdefault("node_name", body.node_name)
        if body.labels:
            spec["labels"] = body.labels
        if body.credential_name is not None:
            spec.setdefault("credential_name", body.credential_name)

        node_id = await _deps.inventory_repo.create_provisioning_placeholder(
            pool_id=pool_id,
            provider="aws",
            instance_class=instance_class,
            instance_type=instance_type,
            node_name=body.node_name,
        )
        job_id = await _deps.provisioning_repo.enqueue(
            node_id=node_id,
            pool_id=pool_id,
            org_id=org_id,
            provider="aws",
            spec=spec,
        )
        return AddProviderNodeResponse(
            node_id=str(node_id),
            provider="aws",
            provider_instance_id=None,
            state="provisioning",
            job_id=str(job_id),
        )

    # ------------------------------------------------------------------
    # Legacy adapter path (nosana, akash). Calls the synchronous
    # provision_single_node which still blocks on the provider SDK.
    # ------------------------------------------------------------------
    adapter = _deps.adapters.get(provider)
    if adapter is None:
        raise HTTPException(404, f"unknown provider: {provider}")

    org_id = _org_id_from_headers(authorization, x_organization_id)
    pool_id = str(await _deps.pool_repo.ensure_default_pool(org_id=org_id))

    spec = dict(body.spec or {})
    # Fold top-level convenience fields into the spec so adapters see them
    # uniformly. The Nosana add-node UI sends gpu_type / market_address etc.
    # alongside spec; mirror them in.
    if body.node_name is not None:
        spec.setdefault("node_name", body.node_name)
    if body.labels:
        spec["labels"] = body.labels
    if body.credential_name is not None:
        spec.setdefault("credential_name", body.credential_name)

    try:
        node = await adapter.provision_single_node(
            pool_id=pool_id, org_id=org_id, spec=spec,
        )
    except NotImplementedError:
        raise HTTPException(
            501, f"provider {provider!r} does not support single-node add",
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("provision_single_node failed")
        raise HTTPException(502, f"{provider} adapter error: {e}")

    return AddProviderNodeResponse(
        node_id=str(node["id"]),
        provider=node.get("provider", provider),
        provider_instance_id=node.get("provider_instance_id"),
        state=node.get("state", "provisioning"),
    )


# ---------------------------------------------------------------------------
# Provisioning / EC2 console endpoints.
# ---------------------------------------------------------------------------


@router.get("/{node_id}/provisioning", response_model=ProvisioningSummary)
async def get_provisioning(
    node_id: str = Path(...),
    _granted: bool = Depends(_need_perm("deployment:list")),
):
    row = await _deps.inventory_repo.get_node(node_id=node_id)
    if not row:
        raise HTTPException(404, "node not found")
    pool_id = row.get("pool_id")

    # Pull the most-recent provisioning_jobs row for this node. The new
    # state-machine path uses this row as the authoritative source of
    # current_phase / attempt_count / error_*; the legacy event-log path
    # below only contributes the phase history list.
    job = None
    if _deps.provisioning_repo is not None and hasattr(
        _deps.provisioning_repo, "get_by_node",
    ):
        try:
            node_uuid = uuid.UUID(str(node_id))
        except (ValueError, TypeError):
            node_uuid = None
        if node_uuid is not None:
            job = await _deps.provisioning_repo.get_by_node(node_id=node_uuid)

    # Build the error block. Populated only when a job exists AND its
    # last_error_code is set — i.e. the reconciler classified an error
    # against it. Default error_class to PERMANENT for the rare case
    # where a code/message is recorded without a classification.
    error_block: dict[str, Any] | None = None
    if job is not None and getattr(job, "last_error_code", None):
        ec = getattr(job, "error_class", None)
        error_block = {
            "code": job.last_error_code,
            "message": getattr(job, "last_error_message", None),
            "hint": getattr(job, "last_error_hint", None),
            "class": ec.value if ec is not None else "PERMANENT",
        }

    # AWS metadata grid. Always shaped for aws nodes (even pre-pulumi);
    # outputs that haven't landed yet show up as None and the UI hides
    # individual rows. instance_class / instance_type come from the
    # inventory row (committed at add-node time); region / ami_id /
    # instance_id / public_dns come from the Pulumi stack outputs once
    # PulumiUpHandler has merged them in.
    aws_metadata: dict[str, Any] | None = None
    if row.get("provider") == "aws":
        outs = (getattr(job, "pulumi_stack_outputs", None) or {}) if job else {}
        aws_metadata = {
            "instance_class": row.get("instance_class"),
            "instance_type":  row.get("instance_type"),
            "region":         outs.get("region"),
            "ami_id":         outs.get("ami_id"),
            "instance_id":    outs.get("instance_id"),
            "public_dns":     outs.get("public_dns"),
        }

    # Phases summary via the existing event log. Repo may be None for
    # nodes that predate the provisioning queue (worker / nosana / akash),
    # in which case the phases list is empty.
    phases: list[ProvisioningPhase] = []
    if _deps.provisioning_repo is not None and pool_id and hasattr(
        _deps.provisioning_repo, "summarize_phases",
    ):
        summary = await _deps.provisioning_repo.summarize_phases(pool_id=pool_id)
        phases = [ProvisioningPhase(
            phase=p["phase"], status=p["status"],
            started_at=p["started_at"].isoformat() if p["started_at"] else None,
            ended_at=p["ended_at"].isoformat() if p["ended_at"] else None,
            last_message=p["last_message"],
        ) for p in summary]

    # current_phase / terminal: prefer the job row when present (the
    # state-machine path), fall back to the legacy current_phase repo
    # call for nodes that don't have a job row (worker / nosana / akash).
    if job is not None:
        current_phase = job.phase.value
        terminal = job.phase.is_terminal
    elif _deps.provisioning_repo is not None and pool_id and hasattr(
        _deps.provisioning_repo, "current_phase",
    ):
        current_phase = await _deps.provisioning_repo.current_phase(pool_id=pool_id)
        node_state = row.get("state")
        terminal = current_phase is None or node_state in ("ready", "terminated")
    else:
        current_phase = None
        terminal = True

    return ProvisioningSummary(
        current_phase=current_phase,
        terminal=terminal,
        phases=phases,
        attempt_count=getattr(job, "attempt_count", 0) if job is not None else 0,
        error=error_block,
        aws_metadata=aws_metadata,
        job_id=str(job.id) if job is not None else None,
    )


@router.get("/{node_id}/provisioning-logs", response_model=ProvisioningLogsResponse)
async def get_provisioning_logs(
    node_id: str = Path(...),
    after: int = Query(default=0, ge=0),
    limit: int = Query(default=500, ge=1, le=2000),
    _granted: bool = Depends(_need_perm("deployment:list")),
):
    row = await _deps.inventory_repo.get_node(node_id=node_id)
    if not row:
        raise HTTPException(404, "node not found")
    pool_id = row.get("pool_id")
    if _deps.provisioning_repo is None or not pool_id:
        return ProvisioningLogsResponse(events=[], next_after=None)
    events = await _deps.provisioning_repo.list_events_after(
        pool_id=pool_id, after_id=after, limit=limit,
    )
    next_after = events[-1]["id"] if events else None
    return ProvisioningLogsResponse(
        events=[ProvisioningEvent(
            id=e["id"], phase=e["phase"], status=e["status"],
            message=e["message"],
            created_at=e["created_at"].isoformat(),
        ) for e in events],
        next_after=next_after,
    )


@router.get("/{node_id}/ec2-console", response_model=EC2ConsoleResponse)
async def get_ec2_console(
    node_id: str = Path(...),
    _granted: bool = Depends(_need_perm("deployment:list")),
):
    from datetime import datetime, timezone
    row = await _deps.inventory_repo.get_node(node_id=node_id)
    if not row:
        raise HTTPException(404, "node not found")
    if row.get("provider") != "aws":
        raise HTTPException(404, "ec2 console only available for aws provider")
    adapters = getattr(_deps, "adapters", None) or {}
    adapter = adapters.get("aws")
    if adapter is None:
        raise HTTPException(503, "aws adapter not configured")
    instance_id = row.get("provider_instance_id") or ""
    if not instance_id or instance_id.startswith("placeholder:"):
        return EC2ConsoleResponse(
            logs=[], fetched_at=datetime.now(timezone.utc).isoformat(),
        )
    result = await adapter.get_logs(provider_instance_id=instance_id)
    return EC2ConsoleResponse(
        logs=result.get("logs", []),
        fetched_at=datetime.now(timezone.utc).isoformat(),
    )


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


def _to_view(row: dict) -> NodeView:
    last_hb = row.get("last_heartbeat")
    if hasattr(last_hb, "isoformat"):
        last_hb = last_hb.isoformat()
    labels = row.get("labels") or {}
    if isinstance(labels, str):
        import json
        try:
            labels = json.loads(labels)
        except Exception:
            labels = {}
    return NodeView(
        id=str(row["id"]),
        pool_id=str(row.get("pool_id")) if row.get("pool_id") else None,
        node_name=row.get("node_name") or row.get("hostname"),
        agent_kind=row.get("agent_kind"),
        provider=row.get("provider"),
        state=row.get("state") or "unknown",
        labels=labels,
        advertise_url=row.get("advertise_url"),
        expose_url=row.get("expose_url"),
        gpu_total=row.get("gpu_total"),
        gpu_allocated=row.get("gpu_allocated"),
        vcpu_total=row.get("vcpu_total"),
        vcpu_allocated=row.get("vcpu_allocated"),
        ram_gb_total=row.get("ram_gb_total"),
        ram_gb_allocated=row.get("ram_gb_allocated"),
        last_heartbeat=last_hb,
        provider_instance_id=row.get("provider_instance_id"),
    )


def _infer_external_url(request: Request) -> str:
    fp = request.headers.get("x-forwarded-proto")
    fh = request.headers.get("x-forwarded-host")
    if fh:
        return f"{fp or 'http'}://{fh}"
    host = request.headers.get("host")
    if host:
        return f"{request.url.scheme}://{host}"
    return ""


__all__ = ["router", "configure"]

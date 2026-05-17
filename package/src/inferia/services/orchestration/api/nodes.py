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


_deps = _Deps()


def configure(
    *,
    inventory_repo,
    pool_repo,
    worker_auth,
    control_plane_external_url: str,
    adapters: dict[str, Any],
    require_permission,
) -> None:
    _deps.inventory_repo = inventory_repo
    _deps.pool_repo = pool_repo
    _deps.worker_auth = worker_auth
    _deps.control_plane_external_url = control_plane_external_url
    _deps.adapters = dict(adapters)
    _deps.require_permission = require_permission


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


@router.delete("/{node_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_node(
    node_id: str = Path(...),
    _granted: bool = Depends(_need_perm("deployment:delete")),
):
    existing = await _deps.inventory_repo.get_node(node_id=node_id)
    if not existing:
        raise HTTPException(404, "node not found")
    await _deps.inventory_repo.soft_delete_node(node_id=node_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


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
    advertise_url = body.advertise_url or "http://localhost:8080"
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
        "# worker's inference port. localhost is fine for same-host smoke;\n"
        "# change to a routable URL for production.\n"
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

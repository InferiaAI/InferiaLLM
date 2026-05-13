"""
Dashboard-facing admin router for inferia-worker management.

Protected by the existing user-JWT + RBAC system. The dashboard (or any
operator HTTP client) uses these endpoints to:

* mint a one-shot bootstrap token + ready-to-paste env snippet,
* list workers in a pool with their live connection state, and
* revoke a worker (close its WS + mark its inventory row terminated).
"""

from __future__ import annotations

import time
from typing import Any, Callable

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from inferia.services.orchestration.services.worker_controller.auth import (
    WorkerAuth,
)
from inferia.services.orchestration.services.worker_controller.registry import (
    WorkerRegistry,
)

router = APIRouter(prefix="/v1/admin/workers")


# ---------------------------------------------------------------------------
# Dependency injection.
# ---------------------------------------------------------------------------


class _Deps:
    worker_auth: WorkerAuth | None = None
    worker_registry: WorkerRegistry | None = None
    inventory_repo: Any = None
    pool_repo: Any = None
    control_plane_external_url: str = ""
    require_permission: Callable[[str], Any] | None = None


_deps = _Deps()


def configure(
    *,
    worker_auth: WorkerAuth,
    worker_registry: WorkerRegistry,
    inventory_repo,
    pool_repo,
    control_plane_external_url: str,
    require_permission: Callable[[str], Any],
) -> None:
    """Wire dependencies at startup. Idempotent."""
    _deps.worker_auth = worker_auth
    _deps.worker_registry = worker_registry
    _deps.inventory_repo = inventory_repo
    _deps.pool_repo = pool_repo
    _deps.control_plane_external_url = control_plane_external_url
    _deps.require_permission = require_permission


def _need_perm(perm: str):
    """Build a FastAPI dependency that enforces ``perm`` at request time.

    The actual permission-check logic is plugged in by
    ``configure(require_permission=...)``. We deliberately bake the
    Authorization header read into a stable FastAPI dependency here
    (rather than delegating to the factory at request time) so that
    FastAPI's signature introspection sees the same shape regardless of
    when ``configure`` runs relative to module import.
    """
    async def _dep(authorization: str | None = Header(default=None)):
        if _deps.require_permission is None:
            raise HTTPException(503, "RBAC dependency not configured")
        check = _deps.require_permission(perm)
        # The factory may produce a sync or async callable. Pass the
        # request's Authorization header to it positionally for backward
        # compat with simple checkers.
        try:
            result = check(authorization)
        except TypeError:
            result = check()
        if hasattr(result, "__await__"):
            result = await result
        return result
    return _dep


def _auth() -> WorkerAuth:
    if _deps.worker_auth is None:
        raise HTTPException(503, "worker auth not configured")
    return _deps.worker_auth


def _registry() -> WorkerRegistry:
    if _deps.worker_registry is None:
        raise HTTPException(503, "worker registry not configured")
    return _deps.worker_registry


def _inventory():
    if _deps.inventory_repo is None:
        raise HTTPException(503, "inventory repo not configured")
    return _deps.inventory_repo


def _pools():
    if _deps.pool_repo is None:
        raise HTTPException(503, "pool repo not configured")
    return _deps.pool_repo


# ---------------------------------------------------------------------------
# Pydantic schemas.
# ---------------------------------------------------------------------------


class MintRequest(BaseModel):
    pool_id: str
    ttl_hours: int = Field(default=1, ge=1, le=24)


class MintResponse(BaseModel):
    bootstrap_token: str
    expires_at: int
    pool_id: str
    control_plane_url: str
    inference_token: str
    env_snippet: str


class WorkerView(BaseModel):
    node_id: str
    node_name: str | None = None
    advertise_url: str | None = None
    agent_kind: str = "worker"
    state: str
    connected: bool
    last_heartbeat: str | None = None
    used: dict[str, str] = Field(default_factory=dict)
    loaded_models: list[str] = Field(default_factory=list)
    allocatable: dict[str, str] = Field(default_factory=dict)


class ListResponse(BaseModel):
    workers: list[WorkerView]


# ---------------------------------------------------------------------------
# Endpoints.
# ---------------------------------------------------------------------------


@router.post("/tokens", response_model=MintResponse)
async def mint_bootstrap_token(
    body: MintRequest,
    request: Request,
    _granted: bool = Depends(_need_perm("deployment:create")),
):
    pool_repo = _pools()
    pool = await pool_repo.get(body.pool_id)
    if pool is None:
        raise HTTPException(status_code=404, detail="pool not found")
    if pool.get("lifecycle_state") in ("terminating", "terminated"):
        raise HTTPException(
            status_code=409,
            detail="pool is terminating, cannot add workers",
        )

    inference_token = await pool_repo.get_or_generate_inference_token(
        pool_id=body.pool_id,
    )
    bootstrap_token = _auth().mint_bootstrap_token(
        pool_id=body.pool_id,
        ttl_seconds=body.ttl_hours * 3600,
    )
    expires_at = int(time.time()) + body.ttl_hours * 3600

    # Resolve the URL workers will paste into their .env. Configured value
    # wins; otherwise fall back to the URL the client used to reach us
    # (X-Forwarded-* via the api_gateway proxy, then the raw Host header).
    control_plane_url = _deps.control_plane_external_url or _infer_external_url(request)

    env_snippet = _render_env_snippet(
        control_plane_url=control_plane_url,
        bootstrap_token=bootstrap_token,
        pool_id=body.pool_id,
        inference_token=inference_token,
    )
    return MintResponse(
        bootstrap_token=bootstrap_token,
        expires_at=expires_at,
        pool_id=body.pool_id,
        control_plane_url=control_plane_url,
        inference_token=inference_token,
        env_snippet=env_snippet,
    )


@router.get("/pool/{pool_id}", response_model=ListResponse)
async def list_workers(
    pool_id: str,
    _granted: bool = Depends(_need_perm("deployment:read")),
):
    rows = await _inventory().list_workers(pool_id=pool_id)
    live = set(_registry().list_nodes())
    workers = [_row_to_view(r, connected=str(r["id"]) in live) for r in rows]
    return ListResponse(workers=workers)


@router.delete("/{node_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_worker(
    node_id: str,
    _granted: bool = Depends(_need_perm("deployment:delete")),
):
    # Verify the node exists. Return 404 if not.
    node = await _inventory().get_node_by_id(node_id)
    if node is None:
        raise HTTPException(status_code=404, detail="node not found")

    await _inventory().mark_terminated_worker(node_id=node_id)

    conn = _registry().get(node_id)
    if conn is not None:
        try:
            await conn.ws.close(1008, "revoked by admin")
        except Exception:
            pass
        try:
            await _registry().detach(node_id, conn.ws)
        except Exception:
            pass

    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


def _infer_external_url(request: Request) -> str:
    """Best-effort: derive an externally-reachable URL from the request the
    dashboard made. Used when CONTROL_PLANE_EXTERNAL_URL is not set."""
    forwarded_proto = request.headers.get("x-forwarded-proto")
    forwarded_host = request.headers.get("x-forwarded-host")
    if forwarded_host:
        proto = forwarded_proto or "http"
        return f"{proto}://{forwarded_host}"
    host = request.headers.get("host")
    if host:
        # request.url.scheme reflects the local connection scheme (likely
        # http inside docker). If the api_gateway terminates TLS upstream
        # the operator should set CONTROL_PLANE_EXTERNAL_URL explicitly.
        return f"{request.url.scheme}://{host}"
    return ""


def _render_env_snippet(
    *,
    control_plane_url: str,
    bootstrap_token: str,
    pool_id: str,
    inference_token: str,
) -> str:
    """Build the multi-line .env block returned by the mint endpoint.

    Two values are left blank for the operator (NODE_NAME and
    WORKER_ADVERTISE_URL) — these are deployment-specific and the dashboard
    cannot know them in advance.
    """
    return (
        f"CONTROL_PLANE_URL={control_plane_url}\n"
        f"BOOTSTRAP_TOKEN={bootstrap_token}\n"
        f"POOL_ID={pool_id}\n"
        f"NODE_NAME=  # operator fills in\n"
        f"WORKER_ADVERTISE_URL=  # operator fills in\n"
        f"INFERENCE_TOKEN={inference_token}\n"
    )


def _row_to_view(row: dict, *, connected: bool) -> WorkerView:
    metadata = row.get("metadata") or {}
    if isinstance(metadata, str):
        import json
        try:
            metadata = json.loads(metadata)
        except Exception:
            metadata = {}

    used = metadata.get("used") if isinstance(metadata, dict) else None
    loaded = metadata.get("loaded_models") if isinstance(metadata, dict) else None

    return WorkerView(
        node_id=str(row["id"]),
        node_name=row.get("node_name"),
        advertise_url=row.get("advertise_url"),
        agent_kind=row.get("agent_kind") or "worker",
        state=row.get("state") or "unknown",
        connected=connected,
        last_heartbeat=(
            row["last_heartbeat"].isoformat()
            if row.get("last_heartbeat") and hasattr(row["last_heartbeat"], "isoformat")
            else row.get("last_heartbeat")
        ),
        used=used if isinstance(used, dict) else {},
        loaded_models=loaded if isinstance(loaded, list) else [],
        allocatable={
            "gpu": str(row.get("gpu_total") or 0),
            "cpu": str(row.get("vcpu_total") or 0),
            "memory_gb": str(row.get("ram_gb_total") or 0),
        },
    )


__all__ = ["router", "configure"]

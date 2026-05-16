"""
Dashboard-facing admin router for inferia-worker management.

Protected by the existing user-JWT + RBAC system. The dashboard (or any
operator HTTP client) uses these endpoints to:

* mint a one-shot bootstrap token + ready-to-paste env snippet,
* list workers in a pool with their live connection state,
* revoke a worker (close its WS + mark its inventory row terminated),
* and proxy live logs / interactive shell WebSockets to the worker.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any, Callable
from urllib.parse import urlsplit, urlunsplit

import websockets
from fastapi import (
    APIRouter,
    Depends,
    Header,
    HTTPException,
    Request,
    Response,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from pydantic import BaseModel, Field

from inferia.services.orchestration.services.worker_controller.auth import (
    WorkerAuth,
)
from inferia.services.orchestration.services.worker_controller.registry import (
    WorkerRegistry,
)

logger = logging.getLogger(__name__)

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
# Live logs / interactive shell WS proxies.
# ---------------------------------------------------------------------------
#
# The dashboard opens ws://<gateway>/api/v1/admin/workers/{node_id}/logs (and
# /shell) over its existing user-JWT auth. The gateway proxies WS frames to
# orchestration's /v1/admin/workers/{node_id}/logs, and this endpoint then
# forwards them to the worker's own ws://<worker>/v1/logs endpoint, which is
# authenticated with the worker pool's INFERENCE_TOKEN. The dashboard never
# sees that token — it stays server-side.


async def _resolve_worker_ws_base(node_id: str) -> tuple[str, str]:
    """Return (ws_base_url, inference_token) for the worker that owns node_id.

    Raises HTTPException if the node doesn't exist or has no reachable URL.
    The advertise_url may legitimately be http://localhost:8080 in a
    same-host dev setup; from inside the orchestration container that
    resolves to orchestration itself, so we substitute with the worker
    compose service name `inferia-worker:8080` when localhost is detected.
    Operators in production should set WORKER_ADVERTISE_URL to a routable
    URL that the orchestration container can resolve.
    """
    node = await _inventory().get_node_by_id(node_id)
    if node is None:
        raise HTTPException(status_code=404, detail="node not found")
    if node.get("agent_kind") != "worker":
        raise HTTPException(status_code=400, detail="not a worker node")

    raw = node.get("advertise_url") or ""
    if not raw:
        raise HTTPException(
            status_code=409,
            detail="worker has no advertise_url; logs/shell are unavailable",
        )

    parts = urlsplit(raw)
    host = parts.hostname or ""
    port = parts.port or 8080
    if host in ("localhost", "127.0.0.1", "0.0.0.0"):
        # Dev fallback — see docstring. Without this every same-host docker
        # compose user would have to manually rewrite their .env.
        host = os.getenv("WORKER_LOCAL_FALLBACK_HOST", "inferia-worker")

    scheme = "wss" if parts.scheme == "https" else "ws"
    ws_base = urlunsplit((scheme, f"{host}:{port}", "", "", ""))

    pool_id = node.get("pool_id")
    if not pool_id:
        raise HTTPException(status_code=500, detail="worker missing pool_id")
    pool_repo = _deps.pool_repo
    if pool_repo is None:
        raise HTTPException(503, "pool repo not configured")
    token = await pool_repo.get_or_generate_inference_token(pool_id=str(pool_id))
    return ws_base, token


async def _proxy_ws_text(client_ws: WebSocket, upstream_url: str, headers: dict[str, str]) -> None:
    """Bi-directional text/binary WebSocket relay.

    Closes both sides on first disconnect. Used by /logs and /shell.
    """
    try:
        upstream = await websockets.connect(upstream_url, additional_headers=headers)
    except Exception as e:
        await client_ws.send_json({"type": "error", "message": f"upstream connect failed: {e}"})
        await client_ws.close()
        return

    async def c2u():
        try:
            while True:
                msg = await client_ws.receive()
                t = msg.get("type")
                if t == "websocket.disconnect":
                    break
                if msg.get("text") is not None:
                    await upstream.send(msg["text"])
                elif msg.get("bytes") is not None:
                    await upstream.send(msg["bytes"])
        except WebSocketDisconnect:
            pass
        except Exception as e:
            logger.warning("c2u relay error: %s", e)
        finally:
            await upstream.close()

    async def u2c():
        try:
            async for frame in upstream:
                if isinstance(frame, bytes):
                    await client_ws.send_bytes(frame)
                else:
                    await client_ws.send_text(frame)
        except Exception as e:
            logger.warning("u2c relay error: %s", e)
        finally:
            try:
                await client_ws.close()
            except Exception:
                pass

    await asyncio.gather(c2u(), u2c(), return_exceptions=True)


@router.websocket("/{node_id}/logs")
async def proxy_worker_logs(ws: WebSocket, node_id: str):
    """Forward the dashboard's /logs WebSocket through to the worker."""
    await ws.accept()
    try:
        ws_base, token = await _resolve_worker_ws_base(node_id)
    except HTTPException as e:
        await ws.send_json({"type": "error", "message": e.detail})
        await ws.close()
        return

    deployment = ws.query_params.get("deployment") or ""
    container = ws.query_params.get("container") or ""
    q = []
    if deployment:
        q.append(f"deployment={deployment}")
    if container:
        q.append(f"container={container}")
    qs = ("?" + "&".join(q)) if q else ""
    upstream_url = f"{ws_base}/v1/logs{qs}"
    headers = {"Authorization": f"Bearer {token}"}
    await _proxy_ws_text(ws, upstream_url, headers)


@router.websocket("/{node_id}/shell")
async def proxy_worker_shell(ws: WebSocket, node_id: str):
    """Forward the dashboard's /shell WebSocket through to the worker."""
    await ws.accept()
    try:
        ws_base, token = await _resolve_worker_ws_base(node_id)
    except HTTPException as e:
        await ws.send_json({"type": "error", "message": e.detail})
        await ws.close()
        return

    deployment = ws.query_params.get("deployment") or ""
    container = ws.query_params.get("container") or ""
    q = []
    if deployment:
        q.append(f"deployment={deployment}")
    if container:
        q.append(f"container={container}")
    qs = ("?" + "&".join(q)) if q else ""
    upstream_url = f"{ws_base}/v1/shell{qs}"
    headers = {"Authorization": f"Bearer {token}"}
    await _proxy_ws_text(ws, upstream_url, headers)


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

    Two values are placeholders the operator typically overrides
    (NODE_NAME, WORKER_ADVERTISE_URL). Comments live on their own lines —
    docker-compose treats everything after '=' as the literal value, so
    inline ' # comment' suffixes end up as the value and break boot.
    """
    # CONTROL_PLANE_URL: prefer the explicit value passed in; if empty,
    # default to the docker service name (works when the worker compose
    # attaches to deploy_inferia-net on the same docker host).
    cp = control_plane_url or os.getenv(
        "WORKER_DEFAULT_CONTROL_PLANE_URL", "http://inferia-app:8000"
    )
    return (
        "# Generated by InferiaLLM. Bootstrap token expires in 1h.\n"
        "# CONTROL_PLANE_URL must be reachable from THIS worker host.\n"
        "# The default works when the worker compose runs on the same\n"
        "# Docker host as InferiaLLM (the worker container resolves\n"
        "# 'inferia-app' via the deploy_inferia-net network).\n"
        f"CONTROL_PLANE_URL={cp}\n"
        f"BOOTSTRAP_TOKEN={bootstrap_token}\n"
        f"POOL_ID={pool_id}\n"
        "# NODE_NAME: change to a unique name for this GPU host.\n"
        "NODE_NAME=my-gpu-node\n"
        "# WORKER_ADVERTISE_URL: URL the control plane reaches this worker's\n"
        "# inference port at. localhost is fine for same-host smoke; use a\n"
        "# routable URL for production.\n"
        "WORKER_ADVERTISE_URL=http://localhost:8080\n"
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

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
import uuid
from typing import Any, Callable

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
from inferia.services.orchestration.services.worker_controller.protocol import (
    LogsEndBody,
    LogsLineBody,
    LogsOpenBody,
    ShellErrorBody,
    ShellExitBody,
    ShellOpenBody,
    ShellOutputBody,
)
from inferia.services.orchestration.services.worker_controller.registry import (
    WorkerNotConnectedError,
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
    db_pool: Any = None


_deps = _Deps()


def configure(
    *,
    worker_auth: WorkerAuth,
    worker_registry: WorkerRegistry,
    inventory_repo,
    pool_repo,
    control_plane_external_url: str,
    require_permission: Callable[[str], Any],
    db_pool: Any = None,
) -> None:
    """Wire dependencies at startup. Idempotent."""
    _deps.worker_auth = worker_auth
    _deps.worker_registry = worker_registry
    _deps.inventory_repo = inventory_repo
    _deps.pool_repo = pool_repo
    _deps.control_plane_external_url = control_plane_external_url
    _deps.require_permission = require_permission
    _deps.db_pool = db_pool


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


@router.delete("/{node_id}")
async def revoke_worker(
    node_id: str,
    _granted: bool = Depends(_need_perm("deployment:delete")),
):
    # Verify the node exists. Return 404 if not.
    node = await _inventory().get_node_by_id(node_id)
    if node is None:
        raise HTTPException(status_code=404, detail="node not found")

    # AWS branch: kick the destroy task and return 202 instead of the
    # legacy 204 "soft delete only" path. The EC2 stack tear-down can
    # take up to 90s and runs in the background.
    provider = node.get("provider") if isinstance(node, dict) else None
    pool_id = node.get("pool_id") if isinstance(node, dict) else None
    if provider == "aws" and _deps.db_pool is not None and pool_id:
        from inferia.services.orchestration.services.adapter_engine import (
            aws_deprovision,
        )
        if hasattr(_inventory(), "mark_terminating_node"):
            await _inventory().mark_terminating_node(node_id=node_id)
        # Also tear down the open WS now so the worker stops heartbeating.
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
        aws_deprovision._spawn_destroy(
            pool_id=str(pool_id),
            node_id=str(node_id),
            db_pool=_deps.db_pool,
        )
        import json as _json
        return Response(
            content=_json.dumps({"node_id": str(node_id), "state": "terminating"}),
            media_type="application/json",
            status_code=status.HTTP_202_ACCEPTED,
        )

    # Legacy path (workers, nosana, akash, gcp/azure for now).
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
# multiplexes those frames over the long-lived worker→CP control channel
# (``/v1/workers/channel``) using a CP-minted ``stream_id``.
#
# The wire format the dashboard speaks is preserved verbatim:
#   inbound (CP → dashboard):
#     {"type": "output",  "data": "..."}        # shell stdout/stderr chunk
#     {"type": "exit",    "exit_code": N, "reason": "..."}
#     {"type": "log",     "stream": "stdout"|"stderr", "data": "..."}
#     {"type": "error",   "message": "..."}
#   outbound (dashboard → CP):
#     {"type": "stdin",   "data": "..."}        # shell only
#     {"type": "resize",  "rows": N, "cols": N} # shell only
#
# We never expose the inference token or the worker's public 8080 anymore —
# the long-lived worker WS does both jobs.


def _qparam_int(ws: WebSocket, key: str, default: int = 0) -> int:
    """Pluck an integer query param, falling back to ``default`` on missing or
    malformed values. Used for ``cols`` / ``rows`` on the shell open."""
    raw = ws.query_params.get(key)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


async def _run_until_either(*coros) -> None:
    """Run two coroutines concurrently; cancel the other as soon as one
    completes. Exceptions are logged but never propagated — the caller
    (a WS handler) just needs both tasks definitely-stopped so it can
    close the socket.

    Without this the WS proxy would deadlock: the worker→dashboard pump
    breaks on a terminal frame and the dashboard→worker pump stays
    blocked on receive() until the client closes — which it can't,
    because the handler hasn't released the WS yet.
    """
    tasks = [asyncio.ensure_future(c) for c in coros]
    try:
        _done, pending = await asyncio.wait(
            tasks, return_when=asyncio.FIRST_COMPLETED,
        )
        for p in pending:
            p.cancel()
        # Drain cancellations so we don't leave "Task was destroyed
        # but it is pending" warnings on the event loop.
        for p in pending:
            try:
                await p
            except (asyncio.CancelledError, Exception):
                pass
    except Exception as e:
        logger.warning("_run_until_either crashed: %s", e)


@router.websocket("/{node_id}/logs")
async def proxy_worker_logs(ws: WebSocket, node_id: str):
    """Multiplex a live container logs stream over the worker's control WS.

    Opens a logs stream via the registry, drains worker→CP LogsLine / LogsEnd
    bodies into the dashboard wire format, and tears the stream down on
    either side disconnect.
    """
    await ws.accept()

    stream_id = str(uuid.uuid4())
    deployment_id = ws.query_params.get("deployment", "")
    container_id = ws.query_params.get("container", "")
    body = LogsOpenBody(
        stream_id=stream_id,
        deployment_id=deployment_id,
        container_id=container_id,
    )

    try:
        handle = await _registry().open_logs_stream(node_id, body)
    except WorkerNotConnectedError:
        await ws.send_json({"type": "error", "message": "worker offline"})
        await ws.close(code=1011)
        return

    async def dashboard_to_worker():
        """The dashboard never sends control frames on the logs WS today,
        but we still drain to detect disconnect."""
        try:
            while True:
                msg = await ws.receive()
                if msg.get("type") == "websocket.disconnect":
                    break
        except WebSocketDisconnect:
            pass
        except Exception as e:
            logger.warning("logs dashboard->worker drain error: %s", e)

    async def worker_to_dashboard():
        try:
            while True:
                frame = await handle.incoming.get()
                if isinstance(frame, LogsLineBody):
                    await ws.send_json({
                        "type": "log",
                        "stream": frame.stream,
                        "data": frame.data,
                    })
                elif isinstance(frame, LogsEndBody):
                    await ws.send_json({
                        "type": "exit",
                        "reason": frame.reason,
                    })
                    break
                elif isinstance(frame, ShellErrorBody):
                    # Synthetic error injected by registry.detach when the
                    # worker disconnects.
                    await ws.send_json({
                        "type": "error",
                        "message": frame.message,
                    })
                    break
                else:
                    logger.warning(
                        "proxy_worker_logs: unexpected frame type=%s",
                        type(frame).__name__,
                    )
        except Exception as e:
            logger.warning("logs worker->dashboard drain error: %s", e)

    await _run_until_either(dashboard_to_worker(), worker_to_dashboard())
    await _registry().close_stream(stream_id)
    try:
        await ws.close()
    except Exception:
        pass


@router.websocket("/{node_id}/shell")
async def proxy_worker_shell(ws: WebSocket, node_id: str):
    """Multiplex an interactive shell over the worker's control WS.

    Opens a shell stream via the registry, translates the dashboard's
    ``stdin`` / ``resize`` frames into ShellInput / ShellResize envelopes,
    and the worker's ShellOutput / ShellExit / ShellError frames back into
    the dashboard wire format.
    """
    await ws.accept()

    stream_id = str(uuid.uuid4())
    deployment_id = ws.query_params.get("deployment", "")
    container_id = ws.query_params.get("container", "")
    shell = ws.query_params.get("shell", "/bin/sh")
    user = ws.query_params.get("user", "")
    cols = _qparam_int(ws, "cols", 0)
    rows = _qparam_int(ws, "rows", 0)
    body = ShellOpenBody(
        stream_id=stream_id,
        shell=shell,
        user=user,
        deployment_id=deployment_id,
        container_id=container_id,
        cols=cols,
        rows=rows,
    )

    try:
        handle = await _registry().open_shell_stream(node_id, body)
    except WorkerNotConnectedError:
        await ws.send_json({"type": "error", "message": "worker offline"})
        await ws.close(code=1011)
        return

    async def dashboard_to_worker():
        try:
            while True:
                payload = await ws.receive_json()
                kind = payload.get("type")
                if kind == "stdin":
                    data = payload.get("data", "")
                    if not isinstance(data, str):
                        continue
                    await _registry().send_shell_input(stream_id, data)
                elif kind == "resize":
                    # The dashboard sends rows + cols directly. Tolerate
                    # zero / missing values — the worker treats 0 as "leave
                    # alone".
                    try:
                        cols_in = int(payload.get("cols") or 0)
                        rows_in = int(payload.get("rows") or 0)
                    except (TypeError, ValueError):
                        continue
                    await _registry().send_shell_resize(
                        stream_id, cols_in, rows_in,
                    )
                # Unknown frame types are silently ignored — the wire format
                # is small and any extension is a worker-side concern.
        except WebSocketDisconnect:
            pass
        except Exception as e:
            logger.warning("shell dashboard->worker drain error: %s", e)

    async def worker_to_dashboard():
        try:
            while True:
                frame = await handle.incoming.get()
                if isinstance(frame, ShellOutputBody):
                    await ws.send_json({
                        "type": "output",
                        "data": frame.data,
                    })
                elif isinstance(frame, ShellExitBody):
                    await ws.send_json({
                        "type": "exit",
                        "exit_code": frame.exit_code,
                        "reason": frame.reason,
                    })
                    break
                elif isinstance(frame, ShellErrorBody):
                    await ws.send_json({
                        "type": "error",
                        "message": frame.message,
                    })
                    break
                else:
                    logger.warning(
                        "proxy_worker_shell: unexpected frame type=%s",
                        type(frame).__name__,
                    )
        except Exception as e:
            logger.warning("shell worker->dashboard drain error: %s", e)

    await _run_until_either(dashboard_to_worker(), worker_to_dashboard())
    await _registry().close_stream(stream_id)
    try:
        await ws.close()
    except Exception:
        pass


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

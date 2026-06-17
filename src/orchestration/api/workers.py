"""
FastAPI router exposing the inferia-worker control-plane endpoints.

* POST /v1/workers/register — bootstrap-token → worker JWT (issues node_id)
* WS   /v1/workers/channel  — long-lived control channel (heartbeat + cmds)
"""

from __future__ import annotations

import asyncio
import logging
import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, Request, WebSocket, WebSocketDisconnect

from orchestration.workers.worker_controller.auth import (
    InvalidBootstrapToken,
    InvalidTokenError,
    WorkerAuth,
    consume_bootstrap_token as _db_consume_bootstrap_token,
)
from orchestration.workers.worker_controller.protocol import (
    CommandResultBody,
    Envelope,
    HeartbeatBody,
    LogsEndBody,
    LogsLineBody,
    RegisterRequest,
    RegisterResponse,
    ShellErrorBody,
    ShellExitBody,
    ShellOutputBody,
)
from orchestration.workers.worker_controller.registry import (
    WorkerConn,
    WorkerRegistry,
)

logger = logging.getLogger("workers_api")

router = APIRouter()


# Strong refs to in-flight channel-triggered linker tasks so CPython doesn't
# GC them mid-flight (asyncio holds only a weak ref).
_CHANNEL_LINKER_TASKS: set[asyncio.Task] = set()


def _fire_linker_on_channel_ready(ws: WebSocket, node_id_str: str) -> None:
    """Bind PENDING_NODE deploys + load_model on the just-connected worker.

    Fired from worker_channel once the WS control channel is attached (so the
    worker is reachable). Runs as a background task because on_worker_ready
    awaits the LoadModel CommandResult, which the channel read loop delivers —
    blocking on it inline would deadlock.
    """
    try:
        from orchestration.models.model_deployment.deployment_linker import (
            DeploymentLinker,
        )
        from orchestration.repositories.inventory_repo import (
            InventoryRepository,
        )
        from orchestration.repositories.model_deployment_repo import (
            ModelDeploymentRepository,
        )

        db_pool = ws.app.state.pool
        worker_controller = ws.app.state.worker_controller
        event_bus = getattr(ws.app.state, "event_bus", None)
        linker = DeploymentLinker(
            db_pool=db_pool,
            inventory_repo=InventoryRepository(db_pool),
            deployment_repo=ModelDeploymentRepository(db_pool, event_bus=event_bus),
            worker_controller=worker_controller,
        )
        task = asyncio.create_task(linker.on_worker_ready(uuid.UUID(node_id_str)))
        _CHANNEL_LINKER_TASKS.add(task)
        task.add_done_callback(_CHANNEL_LINKER_TASKS.discard)
    except Exception:
        logger.exception(
            "worker_channel: failed to fire linker hook for node=%s", node_id_str,
        )


# ---------------------------------------------------------------------------
# DB-backed bootstrap token consumption (patchable for tests)
# ---------------------------------------------------------------------------

async def _consume_bootstrap_token(conn, *, token: str):
    """Thin wrapper around the DB-backed consume helper.

    Exists as a module-level function so tests can patch it without needing a
    real asyncpg connection.  When ``conn`` is None we open (and close) a
    short-lived asyncpg connection against the orchestration's POSTGRES_DSN
    so the worker bootstrap flow works without the caller having to wire a
    DB pool into this router. Tests can still monkey-patch this function.
    """
    if conn is not None:
        return await _db_consume_bootstrap_token(conn, token=token)
    import asyncpg
    from orchestration.config import settings as _osettings
    own_conn = await asyncpg.connect(dsn=_osettings.postgres_dsn)
    try:
        return await _db_consume_bootstrap_token(own_conn, token=token)
    finally:
        try:
            await own_conn.close()
        except Exception:
            pass


# Injected at app startup; tests override via dependency_overrides.
class _Deps:
    auth: WorkerAuth | None = None
    registry: WorkerRegistry | None = None
    inventory = None  # InventoryRepository-shaped


_deps = _Deps()


def configure(auth: WorkerAuth, registry: WorkerRegistry, inventory) -> None:
    """Wire dependencies at startup. Idempotent."""
    _deps.auth = auth
    _deps.registry = registry
    _deps.inventory = inventory


def get_auth() -> WorkerAuth:
    if _deps.auth is None:
        raise HTTPException(503, "worker auth not configured")
    return _deps.auth


def get_registry() -> WorkerRegistry:
    if _deps.registry is None:
        raise HTTPException(503, "worker registry not configured")
    return _deps.registry


def get_inventory():
    if _deps.inventory is None:
        raise HTTPException(503, "inventory not configured")
    return _deps.inventory


# ---------------------------------------------------------------------------
# POST /v1/workers/register
# ---------------------------------------------------------------------------


@router.post("/v1/workers/register", response_model=RegisterResponse)
async def register_worker(
    body: RegisterRequest,
    request: Request,
    authorization: str = Header(default=""),
    auth: WorkerAuth = Depends(get_auth),
    inventory=Depends(get_inventory),
) -> RegisterResponse:
    # --- Authentication: two paths -------------------------------------------
    # Path A (new): bootstrap_token in request body → DB-backed single-use
    # consume.  No Authorization header required.
    # Path B (legacy): Authorization: Bearer <JWT> → stateless JWT verify.
    # Both paths verify pool_id scope.
    #
    # The inferia-worker Go client always populates BOTH the body field and
    # the Authorization header (omitempty + Set-Header), so in production
    # we resolve which path to take based on whether a DB conn adapter is
    # wired up. Until the DB-consume adapter is wired, we prefer the
    # stateless JWT path whenever an Authorization header is present.
    header_token = _strip_bearer(authorization)

    # Auth: try DB-backed single-use consume first (token in body). If the
    # token wasn't persisted in the DB fall back to stateless JWT verify
    # (Authorization header). The CLI `inferiallm worker token` and the
    # dashboard both mint stateless JWTs via the admin API, while Pulumi-
    # provisioned workers get DB-backed tokens; this fallback handles both.
    authed = False
    if body.bootstrap_token is not None:
        try:
            db_claim = await _consume_bootstrap_token(None, token=body.bootstrap_token)
        except InvalidBootstrapToken:
            pass  # fall through to JWT verify below
        else:
            if str(db_claim.pool_id) != str(body.pool_id):
                raise HTTPException(401, "pool_scope_violation")
            authed = True

    if not authed:
        if not header_token:
            raise HTTPException(401, "invalid_bootstrap_token")
        try:
            claims = auth.verify_bootstrap_token(header_token)
        except InvalidTokenError as e:
            raise HTTPException(401, f"invalid bootstrap token: {e}")
        if claims.pool_id != body.pool_id:
            raise HTTPException(403, "bootstrap token is for a different pool")

    # --- Build cloud-env labels to merge into inventory ----------------------
    labels: dict = {}
    if body.runtime_env:
        labels["runtime_env"] = body.runtime_env
    if body.instance_id:
        labels["instance_id"] = body.instance_id
    if body.region:
        labels["region"] = body.region
    if body.availability_zone:
        labels["availability_zone"] = body.availability_zone

    # Upsert the compute_nodes row. If a row with the same (pool_id, node_name)
    # exists and is kind='worker', re-use its node_id (allows re-registration
    # after token loss). Conflicting kinds raise 409.
    try:
        node = await inventory.upsert_worker(
            pool_id=body.pool_id,
            node_name=body.node_name,
            advertise_url=body.advertise_url,
            allocatable=body.allocatable,
            labels=labels if labels else None,
            group_id=body.group_id,
        )
    except DuplicateNodeError as e:
        raise HTTPException(409, str(e))

    node_id_str = str(node["id"])
    worker_jwt = auth.mint_worker_token(
        node_id=node_id_str,
        pool_id=body.pool_id,
    )

    # NOTE: the DeploymentLinker hook (binding PENDING_NODE deploys +
    # load_model) is NOT fired here. This endpoint RETURNS worker_jwt, which
    # the worker only THEN uses to open the WS control channel
    # (/v1/workers/channel). At register time that channel does not yet exist,
    # so load_model would always raise NodeUnreachableError and mark the
    # deploy FAILED — and it cannot wait for the channel (the worker is
    # blocked on this very response). The linker is therefore fired from
    # worker_channel(), AFTER the channel is attached and reachable.
    return RegisterResponse(node_id=node_id_str, worker_jwt=worker_jwt)


class DuplicateNodeError(Exception):
    """Raised by inventory.upsert_worker when (pool_id, node_name) is taken
    by a non-worker-kind row (cannot be upgraded in place)."""


# ---------------------------------------------------------------------------
# WS /v1/workers/channel
# ---------------------------------------------------------------------------


@router.websocket("/v1/workers/channel")
async def worker_channel(
    ws: WebSocket,
    authorization: str = Header(default=""),
):
    auth = get_auth()
    registry = get_registry()
    inventory = get_inventory()

    token = _strip_bearer(authorization)
    if not token:
        await ws.close(code=1008, reason="missing token")
        return
    try:
        claims = auth.verify_worker_token(token)
    except InvalidTokenError:
        await ws.close(code=1008, reason="invalid token")
        return

    # Refuse the connection if the node has been revoked (state=terminated).
    # The worker's JWT remains technically valid until expiry, but this gate
    # prevents a revoked worker from re-attaching on reconnect.
    node = await inventory.get_node_by_id(claims.sub)
    if node and node.get("state") == "terminated":
        await ws.close(code=1008, reason="node revoked")
        return

    await ws.accept()
    await inventory.mark_ready_worker(node_id=claims.sub)

    conn = WorkerConn(ws=_FastAPIWSAdapter(ws), pool_id=claims.pool_id)
    await registry.attach(claims.sub, conn)

    # The worker's control channel is now live and reachable, so the
    # DeploymentLinker can actually send LoadModel. Fire it HERE (not at HTTP
    # /register, which races the channel — see register_worker). Run it as a
    # background task: on_worker_ready awaits the LoadModel CommandResult,
    # which is delivered by the read loop BELOW (deliver_command_result) — so
    # it must run concurrently, not block the loop.
    _fire_linker_on_channel_ready(ws, claims.sub)

    # Send Hello.
    try:
        await ws.send_json(
            Envelope(
                type="Hello",
                id=str(uuid.uuid4()),
                body={"channel_id": str(uuid.uuid4())},
            ).model_dump()
        )

        # Read loop: route Heartbeat / CommandResult / shell+logs stream
        # frames into the registry + inventory. The control plane sends
        # unsolicited messages here ONLY for LoadModel/UnloadModel commands
        # and shell/logs open/input/resize/close envelopes — the worker
        # never sees those in this read direction.
        while True:
            data = await ws.receive_json()
            env_type = data.get("type")
            body = data.get("body") or {}
            if env_type == "Heartbeat":
                hb = HeartbeatBody.model_validate(body)
                await inventory.update_heartbeat_with_telemetry(
                    node_id=claims.sub,
                    used=hb.used,
                    loaded_models=hb.loaded_models,
                    deploy_metrics=[m.model_dump() for m in hb.deploy_metrics],
                )
                # Buffer the structured telemetry sample for the dashboard
                # Metrics tab. Optional field — older workers omit it.
                if hb.metrics is not None:
                    registry.record_metrics(claims.sub, hb.metrics.model_dump())
            elif env_type == "CommandResult":
                registry.deliver_command_result(
                    CommandResultBody(**body),
                )
            elif env_type == "ShellOutput":
                registry.deliver_stream_frame(ShellOutputBody(**body))
            elif env_type == "ShellExit":
                registry.deliver_stream_frame(ShellExitBody(**body))
            elif env_type == "ShellError":
                registry.deliver_stream_frame(ShellErrorBody(**body))
            elif env_type == "LogsLine":
                registry.deliver_stream_frame(LogsLineBody(**body))
            elif env_type == "LogsEnd":
                registry.deliver_stream_frame(LogsEndBody(**body))
            # Other types ignored: clients aren't expected to send Hello/Ping
            # in the MVP direction. Malformed bodies for known types raise
            # ValidationError, which we let propagate so the worker
            # disconnects + retries (the alternative — swallow + log —
            # masks protocol drift).
    except WebSocketDisconnect:
        logger.info("worker %s disconnected", claims.sub)
    finally:
        await registry.detach(claims.sub, conn.ws)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _strip_bearer(header: str) -> str:
    if not header:
        return ""
    parts = header.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return ""
    return parts[1].strip()


class _FastAPIWSAdapter:
    """Adapts FastAPI's WebSocket to the WebSocketLike protocol the registry
    expects (send_json + close)."""

    def __init__(self, ws: WebSocket):
        self._ws = ws

    async def send_json(self, payload):
        await self._ws.send_json(payload)

    async def close(self, code: int = 1000, reason: str = ""):
        try:
            await self._ws.close(code=code, reason=reason)
        except Exception:
            pass


__all__ = ["router", "configure", "DuplicateNodeError"]

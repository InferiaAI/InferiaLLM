"""
FastAPI router exposing the inferia-worker control-plane endpoints.

* POST /v1/workers/register — bootstrap-token → worker JWT (issues node_id)
* WS   /v1/workers/channel  — long-lived control channel (heartbeat + cmds)
"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, WebSocket, WebSocketDisconnect, status
from pydantic import BaseModel

from inferia.services.orchestration.services.worker_controller.auth import (
    InvalidTokenError,
    WorkerAuth,
)
from inferia.services.orchestration.services.worker_controller.protocol import (
    CommandResultBody,
    Envelope,
    RegisterRequest,
    RegisterResponse,
)
from inferia.services.orchestration.services.worker_controller.registry import (
    WorkerConn,
    WorkerRegistry,
)

logger = logging.getLogger("inferia.workers_api")

router = APIRouter()


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
    authorization: str = Header(default=""),
    auth: WorkerAuth = Depends(get_auth),
    inventory=Depends(get_inventory),
) -> RegisterResponse:
    token = _strip_bearer(authorization)
    if not token:
        raise HTTPException(401, "missing bootstrap token")

    try:
        claims = auth.verify_bootstrap_token(token)
    except InvalidTokenError as e:
        raise HTTPException(401, f"invalid bootstrap token: {e}")

    if claims.pool_id != body.pool_id:
        raise HTTPException(403, "bootstrap token is for a different pool")

    # Upsert the compute_nodes row. If a row with the same (pool_id, node_name)
    # exists and is kind='worker', re-use its node_id (allows re-registration
    # after token loss). Conflicting kinds raise 409.
    try:
        node = await inventory.upsert_worker(
            pool_id=body.pool_id,
            node_name=body.node_name,
            advertise_url=body.advertise_url,
            allocatable=body.allocatable,
        )
    except DuplicateNodeError as e:
        raise HTTPException(409, str(e))

    worker_jwt = auth.mint_worker_token(
        node_id=node["id"],
        pool_id=body.pool_id,
    )
    return RegisterResponse(node_id=node["id"], worker_jwt=worker_jwt)


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

    await ws.accept()
    await inventory.mark_ready(node_id=claims.sub)

    conn = WorkerConn(ws=_FastAPIWSAdapter(ws), pool_id=claims.pool_id)
    await registry.attach(claims.sub, conn)

    # Send Hello.
    try:
        await ws.send_json(
            Envelope(
                type="Hello",
                id=str(uuid.uuid4()),
                body={"channel_id": str(uuid.uuid4())},
            ).model_dump()
        )

        # Read loop: route Heartbeat / CommandResult into the registry +
        # inventory. The control plane never *sends* unsolicited messages
        # here (only commands, which are initiated via WorkerController).
        while True:
            data = await ws.receive_json()
            env_type = data.get("type")
            body = data.get("body") or {}
            if env_type == "Heartbeat":
                used = body.get("used", {})
                loaded = body.get("loaded_models", [])
                await inventory.update_heartbeat(
                    node_id=claims.sub,
                    used=used,
                    loaded_models=loaded,
                )
            elif env_type == "CommandResult":
                registry.deliver_command_result(
                    CommandResultBody(**body),
                )
            # Other types ignored: clients aren't expected to send Hello/Ping
            # in the MVP direction.
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

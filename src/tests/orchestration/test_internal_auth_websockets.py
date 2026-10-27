"""The internal-key check covers websockets, not just HTTP.

Orchestration's admin websockets (worker logs and the interactive shell) carry
no auth of their own, so the middleware is the only thing between an open port
and a root shell on a worker. A browser cannot set the header, but a browser
never reaches this port: the gateway authenticates the user, then opens its own
upstream connection with the key attached.
"""
from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
import pytest

from orchestration.middleware import InternalAuthMiddleware

KEY = "internal-key-32chars-aaaaaaaaaaaa"


def _app():
    app = FastAPI()
    app.add_middleware(
        InternalAuthMiddleware, internal_api_key=KEY,
        skip_paths=["/health", "/v1/workers/channel"],
    )

    @app.websocket("/v1/admin/workers/{node_id}/shell")
    async def shell(ws: WebSocket, node_id: str):
        await ws.accept()
        await ws.send_text("root@worker")

    @app.websocket("/v1/workers/channel")
    async def channel(ws: WebSocket):
        await ws.accept()
        await ws.send_text("worker channel")

    @app.get("/v1/admin/workers")
    async def listing():
        return {"nodes": []}

    return TestClient(app)


def _shell(headers=None):
    with _app().websocket_connect(
        "/v1/admin/workers/n1/shell", headers=headers or {},
    ) as ws:
        return ws.receive_text()


def test_a_shell_without_the_key_is_refused():
    with pytest.raises(WebSocketDisconnect):
        _shell()


def test_a_shell_with_the_wrong_key_is_refused():
    with pytest.raises(WebSocketDisconnect):
        _shell({"X-Internal-API-Key": "wrong-key"})


def test_an_upgrade_header_does_not_substitute_for_the_key():
    # The HTTP path is what a hand-set Upgrade header actually reaches.
    resp = _app().get("/v1/admin/workers", headers={"Upgrade": "websocket"})
    assert resp.status_code == 401


def test_the_gateway_reaches_the_shell_with_the_key():
    assert _shell({"X-Internal-API-Key": KEY}) == "root@worker"


@pytest.mark.parametrize(
    "path",
    ["/deployment/ws", "/v1/admin/workers/x/shell", "/v1/admin/workers/x/logs"],
)
def test_no_unauthenticated_stream_is_on_the_real_skip_list(path):
    from orchestration.middleware import INTERNAL_AUTH_SKIP_PATHS

    assert path not in INTERNAL_AUTH_SKIP_PATHS


def test_the_worker_channel_still_connects_without_the_key():
    # Workers hold a worker JWT, not the internal key, and authenticate
    # themselves inside the route.
    with _app().websocket_connect("/v1/workers/channel") as ws:
        assert ws.receive_text() == "worker channel"


def test_the_xds_dumps_need_the_key_and_discovery_does_not():
    """The route table and debug dump list every worker's host and port.
    Envoy's REST xDS client cannot send the key, so discovery stays open."""
    from orchestration.middleware import INTERNAL_AUTH_SKIP_PATHS

    assert "/v3/route-table" not in INTERNAL_AUTH_SKIP_PATHS
    assert "/v3/debug/resources" not in INTERNAL_AUTH_SKIP_PATHS
    assert "/v3/discovery:clusters" in INTERNAL_AUTH_SKIP_PATHS
    assert "/v3/discovery:endpoints" in INTERNAL_AUTH_SKIP_PATHS

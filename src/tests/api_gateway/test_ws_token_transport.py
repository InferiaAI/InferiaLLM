"""The dashboard's WebSockets carry the session token in Sec-WebSocket-Protocol.

A token in the URL ends up in access logs, proxy logs and browser history, so
the gateway no longer reads one there.
"""

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from api_gateway.app import app
from api_gateway.gateway import proxy_routes
from api_gateway.models import UserContext

NODE = "3f6c2a4e-1d2b-4c5e-8f9a-0b1c2d3e4f50"
PATHS = [f"/v1/admin/workers/{NODE}/logs", f"/v1/admin/workers/{NODE}/shell", "/v1/deployment/ws"]


@pytest.fixture
def seen(monkeypatch):
    """Tokens the gateway tried to authenticate, and upstream connects."""
    record = {"tokens": [], "upstream": []}

    async def _resolve(token):
        record["tokens"].append(token)
        return UserContext(user_id="u1", username="a@b.com", email="a@b.com",
                           roles=["admin"], permissions=["admin:all"], org_id="org1",
                           quota_limit=1, quota_used=0)

    def _connect(*a, **k):
        record["upstream"].append(a)
        raise OSError("no orchestration in this test")

    monkeypatch.setattr(proxy_routes, "_get_ws_user_context", _resolve)
    monkeypatch.setattr(proxy_routes.authz_service, "require_permission", lambda *a: None)
    monkeypatch.setattr(proxy_routes, "websockets", SimpleNamespace(connect=_connect))
    return record


@pytest.mark.parametrize("path", PATHS)
def test_the_token_is_read_from_the_subprotocol(seen, path):
    with TestClient(app).websocket_connect(path, subprotocols=["inferia.v1", "bearer.jwt-1"]) as ws:
        assert ws.accepted_subprotocol == "inferia.v1"  # never the token itself
    assert seen["tokens"] == ["jwt-1"]


@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize("query", ["access_token=jwt-1", "token=jwt-1"])
def test_a_token_in_the_url_is_not_accepted(seen, path, query):
    with pytest.raises(WebSocketDisconnect):
        with TestClient(app).websocket_connect(f"{path}?{query}"):
            pass
    assert seen["tokens"] == [] and seen["upstream"] == []


@pytest.mark.parametrize("protocols", [["bearer.jwt-1"], ["inferia.v1"], ["inferia.v1", "bearer."]])
def test_both_subprotocols_are_needed(seen, protocols):
    """Without inferia.v1 the handshake has nothing to select; without a
    bearer there is nothing to check."""
    with pytest.raises(WebSocketDisconnect):
        with TestClient(app).websocket_connect(PATHS[0], subprotocols=protocols):
            pass
    assert seen["tokens"] == []


def test_a_stale_url_token_is_not_forwarded_upstream(seen):
    with TestClient(app).websocket_connect(
        f"{PATHS[0]}?deployment=d1&token=old", subprotocols=["inferia.v1", "bearer.jwt-1"],
    ):
        pass
    (url,) = seen["upstream"][0][:1]
    assert "deployment=d1" in url and "token" not in url

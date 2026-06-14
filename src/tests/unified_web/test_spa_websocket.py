"""The "/" SPA catch-all must not crash on a stray WebSocket.

A WebSocket whose path matches none of /api, /inf, /v2 falls through to the
SPAStaticFiles mount at "/". Plain StaticFiles asserts scope["type"]=="http"
and would raise AssertionError (unhandled ASGI exception) per stray WS — e.g.
an old-config worker hitting bare /v1/workers/channel, or a probe. SPAStaticFiles
must reject the websocket cleanly instead.
"""

import pytest

from unified_web.spa import SPAStaticFiles


@pytest.mark.asyncio
async def test_websocket_is_closed_not_asserted(tmp_path):
    (tmp_path / "index.html").write_text("<!doctype html><html></html>")
    app = SPAStaticFiles(directory=str(tmp_path), html=True)

    scope = {"type": "websocket", "path": "/v1/workers/channel", "headers": []}
    sent = []

    async def receive():
        return {"type": "websocket.connect"}

    async def send(message):
        sent.append(message)

    # Must NOT raise AssertionError.
    await app(scope, receive, send)

    assert sent and sent[-1]["type"] == "websocket.close"


@pytest.mark.asyncio
async def test_http_still_serves_index(tmp_path):
    (tmp_path / "index.html").write_text("<!doctype html><html>app</html>")
    app = SPAStaticFiles(directory=str(tmp_path), html=True)

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/deep/spa/route",
        "headers": [],
    }
    messages = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        messages.append(message)

    await app(scope, receive, send)

    start = next(m for m in messages if m["type"] == "http.response.start")
    assert start["status"] == 200  # SPA fallback served index.html


@pytest.mark.asyncio
async def test_lifespan_scope_ignored(tmp_path):
    (tmp_path / "index.html").write_text("<!doctype html>")
    app = SPAStaticFiles(directory=str(tmp_path), html=True)

    async def receive():
        return {"type": "lifespan.startup"}

    async def send(message):  # pragma: no cover - should not be called
        raise AssertionError("lifespan scope must be ignored, not handled")

    # Must return without raising.
    await app({"type": "lifespan"}, receive, send)

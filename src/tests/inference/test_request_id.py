"""Every inference request carries an X-Request-ID through to logs and the gateway."""
import uuid

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse

from common.http_client import request_id_ctx
from common.middleware import RequestIDMiddleware
from inference.app import app as inference_app
from inference.client import ApiGatewayClient


def _app():
    app = FastAPI()
    app.add_middleware(RequestIDMiddleware)

    @app.get("/seen")
    async def seen(request: Request):
        return {"ctx": request_id_ctx.get(), "state": request.state.request_id}

    @app.get("/stream")
    async def stream():
        async def chunks():
            for part in ("a", "b", "c"):
                yield f"data: {part}\n\n"
        return StreamingResponse(chunks(), media_type="text/event-stream")

    return app


async def _get(app, path, headers=None):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://inf") as c:
        return await c.get(path, headers=headers)


@pytest.mark.asyncio
async def test_a_request_without_an_id_gets_a_new_one():
    resp = await _get(_app(), "/seen")
    rid = resp.headers["x-request-id"]
    uuid.UUID(rid)
    assert resp.json() == {"ctx": rid, "state": rid}
    assert request_id_ctx.get() is None


@pytest.mark.asyncio
async def test_the_callers_id_is_kept():
    resp = await _get(_app(), "/seen", {"X-Request-ID": "trace-123"})
    assert resp.headers["x-request-id"] == "trace-123"
    assert resp.json()["ctx"] == "trace-123"


@pytest.mark.parametrize("sent", ["x" * 129, "bad id", "a\x7fb"])
@pytest.mark.asyncio
async def test_an_unusable_callers_id_is_replaced(sent):
    resp = await _get(_app(), "/seen", {"X-Request-ID": sent})
    rid = resp.headers["x-request-id"]
    assert rid != sent
    uuid.UUID(rid)


@pytest.mark.asyncio
async def test_a_streamed_response_passes_through_with_the_id():
    resp = await _get(_app(), "/stream", {"X-Request-ID": "trace-9"})
    assert resp.headers["x-request-id"] == "trace-9"
    assert resp.text == "data: a\n\ndata: b\n\ndata: c\n\n"


@pytest.mark.asyncio
async def test_the_inference_app_sets_the_id():
    resp = await _get(inference_app, "/health", {"X-Request-ID": "trace-health"})
    assert resp.headers["x-request-id"] == "trace-health"


def test_calls_to_the_gateway_forward_the_id():
    client = ApiGatewayClient()
    assert "X-Request-ID" not in client._get_headers()
    token = request_id_ctx.set("trace-7")
    try:
        assert client._get_headers()["X-Request-ID"] == "trace-7"
    finally:
        request_id_ctx.reset(token)


@pytest.mark.asyncio
async def test_the_log_carries_the_id_of_the_request_being_logged(monkeypatch):
    import asyncio

    client = ApiGatewayClient()
    sent = []

    class _Http:
        async def post(self, path, json, headers):
            sent.append(json)

    monkeypatch.setattr(client, "_get_client", lambda: _Http())
    token = request_id_ctx.set("trace-9")
    try:
        await client.log_inference(deployment_id="d1", user_id="apikey:k1", model="m")
    finally:
        request_id_ctx.reset(token)  # the send runs later, in the context it was created in
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert sent[0]["request_id"] == "trace-9"

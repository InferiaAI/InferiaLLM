"""Chat requests go past installed extensions after the quota check, before the engine."""
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from common.http_client import request_id_ctx
from inference.core.handlers import completion as ch

pytestmark = pytest.mark.asyncio

BODY = {"model": "qwen", "messages": [{"role": "user", "content": "hi"}]}


@pytest.fixture
def handler(monkeypatch):
    order = []
    context = {
        "deployment": {"id": "dep-1", "endpoint": "http://engine.test", "engine": "vllm"},
        "user_id_context": "apikey:k1",
        "org_id": "org-1",
        "rate_limit_config": None,
        "log_payloads": True,
    }
    monkeypatch.setattr(ch.GatewayService, "resolve_context", AsyncMock(return_value=context))

    async def check_quota(user_id, model):
        order.append("quota")

    monkeypatch.setattr(ch.api_gateway_client, "check_quota", check_quota)

    def resolve_upstream(*_a, **_k):
        order.append("engine")
        raise RuntimeError("stop before the engine call")

    monkeypatch.setattr(ch, "resolve_upstream", resolve_upstream)
    seen = []

    async def before_request(**request):
        order.append("extensions")
        seen.append(request)

    monkeypatch.setattr(ch.extensions, "before_request", before_request)
    return order, seen, monkeypatch


async def _handle():
    token = request_id_ctx.set("trace-1")
    try:
        return await ch.CompletionHandler.handle("sk-key", dict(BODY), MagicMock())
    finally:
        request_id_ctx.reset(token)


async def test_extensions_see_the_request_after_the_quota_and_before_the_engine(handler):
    order, seen, _ = handler
    with pytest.raises(RuntimeError, match="stop before the engine"):
        await _handle()
    assert order == ["quota", "extensions", "engine"]
    assert seen == [{
        "request_id": "trace-1", "user_id": "apikey:k1", "org_id": "org-1",
        "deployment_id": "dep-1", "model": "qwen", "request_type": "llm", "body": BODY,
    }]


async def test_a_refusal_stops_the_request_before_the_engine(handler):
    order, _, monkeypatch = handler

    async def refuse(**request):
        order.append("extensions")
        raise HTTPException(status_code=400, detail="refused")

    monkeypatch.setattr(ch.extensions, "before_request", refuse)
    with pytest.raises(HTTPException) as e:
        await _handle()
    assert e.value.status_code == 400
    assert order == ["quota", "extensions"]

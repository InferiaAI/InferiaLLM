"""What a caller sees over HTTP when the model name or the key is wrong."""
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from inference.app import app

MISSING = {
    "valid": False,
    "error": "No deployment for model 'nope' of type 'inference' in this organization",
    "reason": "model_not_found",
}
BAD_KEY = {"valid": False, "error": "Invalid API Key"}

ROUTES = [
    ("/v1/chat/completions", {"model": "nope", "messages": [{"role": "user", "content": "hi"}]}),
    (
        "/v1/chat/completions",
        {"model": "nope", "messages": [{"role": "user", "content": "hi"}], "stream": True},
    ),
    ("/v1/embeddings", {"model": "nope", "input": "hi"}),
]


async def _post(path, body, context):
    transport = httpx.ASGITransport(app=app)
    with patch(
        "inference.core.service.api_gateway_client.resolve_context",
        AsyncMock(return_value=context),
    ):
        async with httpx.AsyncClient(transport=transport, base_url="http://inf") as c:
            return await c.post(path, headers={"Authorization": "Bearer sk-test"}, json=body)


@pytest.mark.asyncio
@pytest.mark.parametrize("path,body", ROUTES)
async def test_unknown_model_is_a_404_naming_the_model(path, body):
    resp = await _post(path, body, MISSING)

    assert resp.status_code == 404
    assert "nope" in resp.json()["detail"]


@pytest.mark.asyncio
@pytest.mark.parametrize("path,body", ROUTES)
async def test_bad_key_is_still_a_401(path, body):
    resp = await _post(path, body, BAD_KEY)

    assert resp.status_code == 401

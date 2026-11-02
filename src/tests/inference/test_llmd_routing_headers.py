"""Trust-boundary tests for metadata sent to an llm-d gateway."""

import importlib

from fastapi import FastAPI
import httpx
import pytest


def test_llmd_headers_ignore_spoofed_client_control_headers(monkeypatch):
    from inference.core import llmd_routing

    monkeypatch.setattr(llmd_routing.uuid, "uuid4", lambda: "edge-request-id")
    headers = llmd_routing.trusted_llmd_headers(
        {"engine": "llm-d-inference-sim"},
        "org-from-auth",
        {
            "x-inferia-request-id": "client-chose-id",
            "x-inferia-priority": "999999",
            "x-llm-d-inference-fairness-id": "other-org",
            "x-gateway-destination-endpoint": "attacker:8000",
        },
    )

    assert headers == {
        "x-inferia-request-id": "edge-request-id",
        "x-inferia-workload-class": "interactive_chat",
        "x-inferia-priority": "80",
        "x-llm-d-inference-fairness-id": "org-from-auth",
    }


def test_non_llmd_deployment_gets_no_llmd_headers():
    from inference.core.llmd_routing import trusted_llmd_headers

    assert trusted_llmd_headers({"engine": "vllm"}, "org", {}) == {}


@pytest.mark.asyncio
async def test_unified_mount_passes_client_headers_only_to_the_trust_boundary(monkeypatch):
    """The mounted /inf app reaches the completion handler with raw headers.

    The handler, not FastAPI's header binding, owns the deny-list.  This guards
    against accidentally bypassing that boundary when the inference app is
    mounted under the unified web app.
    """
    inference_app_module = importlib.import_module("inference.app")
    unified = importlib.import_module("unified_web.app")

    seen = {}

    async def fake_handle(**kwargs):
        seen.update(kwargs)
        return {"ok": True}

    monkeypatch.setattr(inference_app_module, "extract_api_key", lambda *_: "key")
    monkeypatch.setattr(
        inference_app_module.OrchestrationService, "handle_completion", fake_handle
    )
    # Keep the parent/mount topology, but do not start the real gateway while
    # exercising an inference-only request.
    monkeypatch.setattr(unified, "gateway_app", FastAPI())

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=unified.build_unified_app()),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/inf/v1/chat/completions",
            headers={"Authorization": "Bearer key", "x-llm-d-inference-fairness-id": "bad"},
            json={"model": "demo", "messages": [{"role": "user", "content": "hi"}]},
        )

    assert response.status_code == 200
    assert seen["client_headers"]["x-llm-d-inference-fairness-id"] == "bad"

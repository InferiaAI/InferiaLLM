"""Tests for the k8s branch of the log-streaming WebSocket.

The adapter's own streaming is covered in test_k8s_log_streaming.py. These
cover the endpoint around it: that a k8s subscription reaches the adapter with
the right arguments, that lines are framed the way the dashboard expects, and
that a subscription missing its instance is refused rather than hanging.
"""
from fastapi import FastAPI

# Same version skew as test_admin_workers_ws_proxy.py: starlette 0.35.1 passes
# ``app=`` to ``httpx.Client``, which httpx 0.28+ removed.
import httpx as _httpx
_orig_client_init = _httpx.Client.__init__


def _patched_client_init(self, *args, **kwargs):
    kwargs.pop("app", None)
    return _orig_client_init(self, *args, **kwargs)


_httpx.Client.__init__ = _patched_client_init  # type: ignore[assignment]

from fastapi.testclient import TestClient  # noqa: E402

from orchestration.models.model_deployment import deployment_server  # noqa: E402
from orchestration.provisioning.engine import registry  # noqa: E402


class FakeAdapter:
    """Records how it was called and replays a fixed set of lines."""

    def __init__(self, lines):
        self._lines = lines
        self.calls = []

    async def stream_logs(self, *, instance, namespace="default", **_):
        self.calls.append({"instance": instance, "namespace": namespace})
        for line in self._lines:
            yield line


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(deployment_server.router, prefix="/v1")
    return app


def _subscribe(instance="inferia-worker-abc", **extra):
    return {
        "type": "subscribe_logs",
        "provider": "k8s",
        "instance": instance,
        **extra,
    }


def test_lines_are_framed_as_log_messages(monkeypatch):
    adapter = FakeAdapter(["first", "second"])
    monkeypatch.setattr(registry, "get_adapter", lambda _p: adapter)

    with TestClient(_app()).websocket_connect("/v1/deployment/ws") as ws:
        ws.send_json(_subscribe())
        assert ws.receive_json() == {"type": "log", "data": "first"}
        assert ws.receive_json() == {"type": "log", "data": "second"}


def test_adapter_gets_the_instance_and_namespace(monkeypatch):
    adapter = FakeAdapter(["x"])
    monkeypatch.setattr(registry, "get_adapter", lambda _p: adapter)

    with TestClient(_app()).websocket_connect("/v1/deployment/ws") as ws:
        ws.send_json(_subscribe(instance="sts-1", namespace="models"))
        ws.receive_json()

    assert adapter.calls == [{"instance": "sts-1", "namespace": "models"}]


def test_namespace_defaults_when_absent(monkeypatch):
    adapter = FakeAdapter(["x"])
    monkeypatch.setattr(registry, "get_adapter", lambda _p: adapter)

    with TestClient(_app()).websocket_connect("/v1/deployment/ws") as ws:
        ws.send_json(_subscribe())
        ws.receive_json()

    assert adapter.calls[0]["namespace"] == "default"


def test_missing_instance_is_refused(monkeypatch):
    adapter = FakeAdapter(["never sent"])
    monkeypatch.setattr(registry, "get_adapter", lambda _p: adapter)

    with TestClient(_app()).websocket_connect("/v1/deployment/ws") as ws:
        ws.send_json({"type": "subscribe_logs", "provider": "k8s"})
        message = ws.receive_json()

    assert message["type"] == "error"
    assert "instance" in message["message"].lower()
    assert adapter.calls == []


def test_first_message_must_be_a_subscription(monkeypatch):
    monkeypatch.setattr(registry, "get_adapter", lambda _p: FakeAdapter([]))

    with TestClient(_app()).websocket_connect("/v1/deployment/ws") as ws:
        ws.send_json({"type": "something_else"})
        message = ws.receive_json()

    assert message["type"] == "error"


def test_adapter_failure_is_reported_not_swallowed(monkeypatch):
    class Failing:
        async def stream_logs(self, **_):
            raise RuntimeError("no pod found for sts-1 in default")
            yield  # pragma: no cover - makes this an async generator

    monkeypatch.setattr(registry, "get_adapter", lambda _p: Failing())

    with TestClient(_app()).websocket_connect("/v1/deployment/ws") as ws:
        ws.send_json(_subscribe())
        message = ws.receive_json()

    assert message["type"] == "error"
    assert "no pod found" in message["message"]

"""Tests for the WorkerController facade."""

import asyncio

import pytest

from inferia.services.orchestration.services.worker_controller.controller import (
    NodeUnreachableError,
    WorkerController,
)
from inferia.services.orchestration.services.worker_controller.protocol import (
    CommandResultBody,
    Envelope,
)
from inferia.services.orchestration.services.worker_controller.registry import (
    WorkerConn,
    WorkerRegistry,
)


class FakeWS:
    def __init__(self):
        self.sent: list = []
        self.closed = False

    async def send_json(self, payload):
        self.sent.append(payload)

    async def close(self, code: int = 1000, reason: str = ""):
        self.closed = True


def make_load_spec():
    return {
        "deployment_id": "dep-1",
        "recipe": "vllm",
        "model": {"artifact_uri": "hf://o/m"},
        "config": {"dtype": "bfloat16"},
        "gpu_indices": [0],
    }


@pytest.mark.asyncio
async def test_load_model_happy_path():
    reg = WorkerRegistry()
    ws = FakeWS()
    await reg.attach("node-1", WorkerConn(ws=ws, pool_id="p"))

    ctrl = WorkerController(reg, command_timeout=1.0)

    async def reply_after_send():
        # Wait briefly for the controller's frame to land on the fake ws.
        for _ in range(50):
            if ws.sent:
                env = ws.sent[-1]
                reg.deliver_command_result(
                    CommandResultBody(
                        in_reply_to=env["id"], status="ok",
                        endpoint_url="https://worker:8080",
                    )
                )
                return
            await asyncio.sleep(0.01)
        raise AssertionError("no command sent")

    sender = asyncio.create_task(reply_after_send())
    result = await ctrl.load_model("node-1", make_load_spec())
    await sender
    assert result.status == "ok"
    assert result.endpoint_url == "https://worker:8080"


@pytest.mark.asyncio
async def test_load_model_node_offline_raises():
    reg = WorkerRegistry()
    ctrl = WorkerController(reg)
    with pytest.raises(NodeUnreachableError):
        await ctrl.load_model("no-such-node", make_load_spec())


@pytest.mark.asyncio
async def test_load_model_timeout():
    reg = WorkerRegistry()
    ws = FakeWS()
    await reg.attach("node-1", WorkerConn(ws=ws, pool_id="p"))
    # Use a very short timeout so the test doesn't drag.
    ctrl = WorkerController(reg, command_timeout=0.05)
    with pytest.raises(asyncio.TimeoutError):
        await ctrl.load_model("node-1", make_load_spec())


@pytest.mark.asyncio
async def test_load_model_failure_response():
    reg = WorkerRegistry()
    ws = FakeWS()
    await reg.attach("node-1", WorkerConn(ws=ws, pool_id="p"))
    ctrl = WorkerController(reg, command_timeout=1.0)

    async def reply_failed():
        for _ in range(50):
            if ws.sent:
                env = ws.sent[-1]
                reg.deliver_command_result(
                    CommandResultBody(in_reply_to=env["id"], status="failed",
                                      detail="docker pull failed")
                )
                return
            await asyncio.sleep(0.01)

    task = asyncio.create_task(reply_failed())
    result = await ctrl.load_model("node-1", make_load_spec())
    await task
    assert result.status == "failed"
    assert "docker pull failed" in result.detail


@pytest.mark.asyncio
async def test_unload_model_happy_path():
    reg = WorkerRegistry()
    ws = FakeWS()
    await reg.attach("node-1", WorkerConn(ws=ws, pool_id="p"))
    ctrl = WorkerController(reg, command_timeout=1.0)

    async def reply_ok():
        for _ in range(50):
            if ws.sent:
                env = ws.sent[-1]
                reg.deliver_command_result(
                    CommandResultBody(in_reply_to=env["id"], status="ok")
                )
                return
            await asyncio.sleep(0.01)

    task = asyncio.create_task(reply_ok())
    result = await ctrl.unload_model("node-1", "dep-1")
    await task
    assert result.status == "ok"


@pytest.mark.asyncio
async def test_load_model_validates_uri():
    """Bad artifact URI should be rejected before reaching the worker."""
    reg = WorkerRegistry()
    ctrl = WorkerController(reg)
    spec = make_load_spec()
    spec["model"]["artifact_uri"] = "javascript:bad"
    with pytest.raises(ValueError):
        await ctrl.load_model("node-1", spec)


@pytest.mark.asyncio
async def test_load_model_sanitises_config():
    reg = WorkerRegistry()
    ws = FakeWS()
    await reg.attach("node-1", WorkerConn(ws=ws, pool_id="p"))
    ctrl = WorkerController(reg, command_timeout=1.0)
    spec = make_load_spec()
    spec["config"] = {
        "dtype": "bfloat16",
        "arbitrary_key": "drop me",
        "trust_anything": True,
    }

    async def reply_ok():
        for _ in range(50):
            if ws.sent:
                env = ws.sent[-1]
                reg.deliver_command_result(
                    CommandResultBody(in_reply_to=env["id"], status="ok",
                                      endpoint_url="http://x")
                )
                return
            await asyncio.sleep(0.01)

    task = asyncio.create_task(reply_ok())
    await ctrl.load_model("node-1", spec)
    await task

    sent_body = ws.sent[-1]["body"]
    assert "dtype" in sent_body["config"]
    assert "arbitrary_key" not in sent_body["config"]
    assert "trust_anything" not in sent_body["config"]

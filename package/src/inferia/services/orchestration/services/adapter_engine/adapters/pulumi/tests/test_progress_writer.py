"""ProgressWriter bridges Pulumi-thread callbacks into the asyncio loop.

The writer exposes a synchronous `write(phase, status, message=None)`
method safe to call from the Pulumi thread, plus an async
`write_async(...)` method for in-loop callers (provision_node phases
before stack.up runs).
"""
from __future__ import annotations
import asyncio
import pytest
from uuid import uuid4

from inferia.services.orchestration.services.adapter_engine.adapters.pulumi.progress_writer import (
    ProgressWriter,
)


class StubRepo:
    def __init__(self):
        self.calls = []

    async def append_event(self, **kw):
        self.calls.append(kw)
        return len(self.calls)


@pytest.mark.asyncio
async def test_write_async_appends_event():
    repo = StubRepo()
    pool = uuid4()
    w = ProgressWriter(repo, pool_id=pool, node_id=None)
    await w.write_async("prepare", "running", "loading creds")
    assert repo.calls == [{
        "pool_id": pool, "node_id": None,
        "phase": "prepare", "status": "running", "message": "loading creds",
    }]


@pytest.mark.asyncio
async def test_write_async_optional_message_defaults_none():
    repo = StubRepo()
    w = ProgressWriter(repo, pool_id=uuid4(), node_id=None)
    await w.write_async("prepare", "succeeded")
    assert repo.calls[0]["message"] is None


@pytest.mark.asyncio
async def test_sync_write_from_other_thread_dispatches_to_loop():
    """sync write must enqueue the coroutine onto the captured loop,
    survive being called from a non-event-loop thread, and not block
    the caller forever."""
    import threading
    repo = StubRepo()
    pool = uuid4()
    loop = asyncio.get_running_loop()
    w = ProgressWriter(repo, pool_id=pool, node_id=None, loop=loop)
    done = threading.Event()

    def run_in_thread():
        w.write("pulumi_up", "log", "creating ec2")
        done.set()

    t = threading.Thread(target=run_in_thread)
    t.start()
    # Yield control to the event loop so the scheduled coroutine runs.
    for _ in range(10):
        if repo.calls:
            break
        await asyncio.sleep(0.01)
    done.wait(timeout=1.0)
    t.join(timeout=1.0)
    assert repo.calls[0]["phase"] == "pulumi_up"
    assert repo.calls[0]["status"] == "log"
    assert repo.calls[0]["message"] == "creating ec2"


@pytest.mark.asyncio
async def test_sync_write_swallows_repo_errors():
    """If append_event raises, the write must not propagate to the
    Pulumi thread (would break the up() call)."""
    class BoomRepo:
        async def append_event(self, **kw):
            raise RuntimeError("db gone")
    w = ProgressWriter(BoomRepo(), pool_id=uuid4(), node_id=None,
                       loop=asyncio.get_running_loop())
    import threading
    t = threading.Thread(target=lambda: w.write("pulumi_up", "log", "x"))
    t.start()
    t.join(timeout=1.0)
    await asyncio.sleep(0.05)  # let the scheduled coro hit its except


@pytest.mark.asyncio
async def test_message_truncated_to_1kib():
    repo = StubRepo()
    w = ProgressWriter(repo, pool_id=uuid4(), node_id=None)
    big = "x" * 5000
    await w.write_async("pulumi_up", "log", big)
    assert len(repo.calls[0]["message"]) == 1024


@pytest.mark.asyncio
async def test_message_none_passes_through():
    repo = StubRepo()
    w = ProgressWriter(repo, pool_id=uuid4(), node_id=None)
    await w.write_async("ready", "succeeded", None)
    assert repo.calls[0]["message"] is None


def test_sync_write_drops_event_when_no_loop_captured(caplog):
    """When ProgressWriter is constructed outside any event loop AND
    no loop is passed in, sync write() must log a warning and not
    attempt to schedule anything (would raise without a loop)."""
    import logging
    from inferia.services.orchestration.services.adapter_engine.adapters.pulumi.progress_writer import (
        ProgressWriter,
    )

    class _StubRepo:
        async def append_event(self, **kw): pass

    # No running loop here — this is a plain `def` test, not `async def`.
    w = ProgressWriter(_StubRepo(), pool_id=uuid4(), node_id=None)
    assert w._loop is None
    with caplog.at_level(logging.WARNING):
        w.write("prepare", "running", "x")
    assert any("no loop" in r.message for r in caplog.records)

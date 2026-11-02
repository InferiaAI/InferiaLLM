import asyncio

import pytest


@pytest.mark.asyncio
async def test_usage_worker_retries_after_a_transient_failure(monkeypatch):
    from api_gateway.gateway import usage_stream_worker as worker

    stop = asyncio.Event()
    calls = 0

    async def once(_stop):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ConnectionError("redis restarted")
        stop.set()

    async def no_wait(waiter, **_kwargs):
        waiter.close()
        raise asyncio.TimeoutError

    monkeypatch.setattr(worker, "_run_usage_worker_once", once)
    monkeypatch.setattr(worker.asyncio, "wait_for", no_wait)
    await worker.run_usage_worker(stop)
    assert calls == 2

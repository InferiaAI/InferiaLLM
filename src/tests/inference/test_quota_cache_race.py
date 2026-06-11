"""Tests for quota/context inflight dedup race condition fix.

Verifies that concurrent awaiters on the same cache key do not cause
redundant task creation (which could produce spurious 429 errors).
"""

import asyncio

import pytest
from unittest.mock import AsyncMock, patch


@pytest.fixture
def mock_settings():
    """Provide minimal settings so ApiGatewayClient can be instantiated."""
    with patch("inference.client.settings") as s:
        s.api_gateway_url = "http://localhost:8000"
        s.api_gateway_internal_key = "test-key"
        s.request_timeout = 5
        s.context_cache_maxsize = 10
        s.context_cache_ttl = 30
        s.quota_check_cache_ttl_seconds = 1.0
        s.quota_check_cache_maxsize = 100
        s.gateway_http_max_connections = 100
        s.gateway_http_max_keepalive_connections = 10
        yield s


@pytest.fixture
def gateway_client(mock_settings):
    from inference.client import ApiGatewayClient

    return ApiGatewayClient()


@pytest.mark.asyncio
async def test_concurrent_check_quota_calls_single_task(gateway_client):
    """Multiple concurrent check_quota calls for the same key must create only one task."""
    call_count = 0
    barrier = asyncio.Event()

    async def slow_check(user_id, model):
        nonlocal call_count
        call_count += 1
        # Wait so all callers pile up on the same inflight task
        await barrier.wait()

    gateway_client._check_quota_uncached = slow_check

    # Launch several concurrent callers
    tasks = [
        asyncio.create_task(gateway_client.check_quota("user-1", "gpt-4"))
        for _ in range(5)
    ]

    # Give callers time to register
    await asyncio.sleep(0.05)
    barrier.set()

    await asyncio.gather(*tasks)

    assert call_count == 1, (
        f"Expected exactly 1 call to _check_quota_uncached, got {call_count}"
    )


@pytest.mark.asyncio
async def test_concurrent_resolve_context_calls_single_task(gateway_client):
    """Multiple concurrent resolve_context calls for the same key must create only one task."""
    call_count = 0
    barrier = asyncio.Event()
    fake_data = {"valid": True, "deployment_id": "dep-1"}

    async def slow_resolve(api_key, model, model_type, sandbox=False):
        nonlocal call_count
        call_count += 1
        await barrier.wait()
        return fake_data

    gateway_client._resolve_context_uncached = slow_resolve

    tasks = [
        asyncio.create_task(
            gateway_client.resolve_context("key-1", "gpt-4", "inference")
        )
        for _ in range(5)
    ]

    await asyncio.sleep(0.05)
    barrier.set()

    results = await asyncio.gather(*tasks)

    assert call_count == 1, (
        f"Expected exactly 1 call to _resolve_context_uncached, got {call_count}"
    )
    for r in results:
        assert r == fake_data


@pytest.mark.asyncio
async def test_sequential_check_quota_after_completion(gateway_client):
    """After a quota check completes, a new call for the same key with expired
    cache must create a fresh task (not reuse a stale one)."""
    call_count = 0

    async def instant_check(user_id, model):
        nonlocal call_count
        call_count += 1

    gateway_client._check_quota_uncached = instant_check
    # Disable TTL cache so each top-level call goes through inflight logic
    gateway_client.quota_cache_ttl = 0

    await gateway_client.check_quota("user-1", "gpt-4")
    assert call_count == 1

    await gateway_client.check_quota("user-1", "gpt-4")
    assert call_count == 2, (
        "A new call after the first completed should create a new task"
    )


@pytest.mark.asyncio
async def test_no_redundant_task_after_owner_cleanup(gateway_client):
    """Regression: non-owner coroutines must not trigger a redundant task
    when the owner cleans up the inflight entry before they reach finally."""
    call_count = 0
    step = asyncio.Event()

    async def tracked_check(user_id, model):
        nonlocal call_count
        call_count += 1
        await step.wait()

    gateway_client._check_quota_uncached = tracked_check
    gateway_client.quota_cache_ttl = 0

    # Start 3 concurrent callers
    t1 = asyncio.create_task(gateway_client.check_quota("u", "m"))
    t2 = asyncio.create_task(gateway_client.check_quota("u", "m"))
    t3 = asyncio.create_task(gateway_client.check_quota("u", "m"))

    await asyncio.sleep(0.05)
    step.set()

    await asyncio.gather(t1, t2, t3)

    assert call_count == 1, (
        f"Only 1 underlying check should run, got {call_count}"
    )

    # Now a 4th caller arrives after everything settled -- should get a new task
    step.clear()
    step.set()  # let it finish immediately
    await gateway_client.check_quota("u", "m")
    assert call_count == 2

"""Tests for circuit breaker — error handling layer."""

import asyncio
import time
import pytest
from unittest.mock import patch, AsyncMock

from inferia.common.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerError,
    CircuitBreakerRegistry,
    CircuitState,
)


@pytest.fixture
def breaker():
    return CircuitBreaker(failure_threshold=3, recovery_timeout=1.0, name="test")


@pytest.mark.asyncio
class TestCircuitBreakerStates:
    """Circuit breaker state transitions."""

    async def test_initial_state_is_closed(self, breaker):
        assert breaker.state == CircuitState.CLOSED

    async def test_success_keeps_closed(self, breaker):
        @breaker
        async def ok():
            return "ok"

        await ok()
        assert breaker.state == CircuitState.CLOSED

    async def test_single_failure_stays_closed(self, breaker):
        @breaker
        async def fail():
            raise Exception("boom")

        with pytest.raises(Exception):
            await fail()
        assert breaker.state == CircuitState.CLOSED
        assert breaker._failure_count == 1

    async def test_failures_at_threshold_opens_circuit(self, breaker):
        @breaker
        async def fail():
            raise Exception("boom")

        for _ in range(3):
            with pytest.raises(Exception):
                await fail()

        assert breaker.state == CircuitState.OPEN

    async def test_open_rejects_immediately(self, breaker):
        @breaker
        async def fail():
            raise Exception("boom")

        # Trip the breaker
        for _ in range(3):
            with pytest.raises(Exception):
                await fail()

        # Now it should reject
        with pytest.raises(CircuitBreakerError):
            await fail()

    async def test_recovery_timeout_transitions_to_half_open(self):
        breaker = CircuitBreaker(failure_threshold=1, recovery_timeout=0.1, name="fast")

        @breaker
        async def fail():
            raise Exception("boom")

        with pytest.raises(Exception):
            await fail()
        assert breaker.state == CircuitState.OPEN

        # Wait for recovery timeout
        await asyncio.sleep(0.15)

        # The next _can_execute should transition to HALF_OPEN
        can = await breaker._can_execute()
        assert can is True
        assert breaker.state == CircuitState.HALF_OPEN

    async def test_success_in_half_open_closes_circuit(self):
        breaker = CircuitBreaker(failure_threshold=1, recovery_timeout=0.1, name="recover")

        call_count = 0

        @breaker
        async def flaky():
            nonlocal call_count
            call_count += 1
            if call_count <= 1:
                raise Exception("first call fails")
            return "ok"

        with pytest.raises(Exception):
            await flaky()  # call 1: fails, opens circuit

        await asyncio.sleep(0.15)  # wait for recovery

        result = await flaky()  # call 2: succeeds in half-open
        assert result == "ok"
        assert breaker.state == CircuitState.CLOSED
        assert breaker._failure_count == 0

    async def test_failure_in_half_open_reopens(self):
        breaker = CircuitBreaker(failure_threshold=1, recovery_timeout=0.1, name="reopen")

        @breaker
        async def fail():
            raise Exception("still broken")

        with pytest.raises(Exception):
            await fail()
        assert breaker.state == CircuitState.OPEN

        await asyncio.sleep(0.15)

        with pytest.raises(Exception):
            await fail()  # fails in half-open
        assert breaker.state == CircuitState.OPEN

    async def test_only_expected_exception_counts(self):
        """Only the specified exception type trips the breaker."""
        breaker = CircuitBreaker(
            failure_threshold=1,
            expected_exception=ValueError,
            name="selective",
        )

        @breaker
        async def raise_type_error():
            raise TypeError("wrong type")

        # TypeError should propagate but NOT count as failure
        with pytest.raises(TypeError):
            await raise_type_error()
        assert breaker.state == CircuitState.CLOSED
        assert breaker._failure_count == 0

    async def test_unexpected_exception_propagates(self):
        breaker = CircuitBreaker(
            failure_threshold=1,
            expected_exception=ValueError,
            name="propagate",
        )

        @breaker
        async def raise_runtime():
            raise RuntimeError("unexpected")

        with pytest.raises(RuntimeError):
            await raise_runtime()
        # Should NOT have tripped
        assert breaker.state == CircuitState.CLOSED

    async def test_decorator_passes_args_kwargs(self):
        breaker = CircuitBreaker(name="args")

        @breaker
        async def add(a, b, extra=0):
            return a + b + extra

        result = await add(1, 2, extra=10)
        assert result == 13

    async def test_failure_count_resets_on_success(self, breaker):
        call_count = 0

        @breaker
        async def flaky():
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                raise Exception("fail")
            return "ok"

        # 2 failures
        for _ in range(2):
            with pytest.raises(Exception):
                await flaky()
        assert breaker._failure_count == 2

        # 1 success resets
        await flaky()
        assert breaker._failure_count == 0

    async def test_recovery_timeout_boundary(self):
        """At exactly recovery_timeout, should transition to HALF_OPEN."""
        breaker = CircuitBreaker(failure_threshold=1, recovery_timeout=10.0, name="boundary")

        @breaker
        async def fail():
            raise Exception("fail")

        with pytest.raises(Exception):
            await fail()

        base_time = breaker._last_failure_time

        # Just before timeout: still OPEN
        with patch("inferia.common.circuit_breaker.time") as mock_time:
            mock_time.time.return_value = base_time + 9.999
            can = await breaker._can_execute()
            assert can is False

        # At exactly timeout: transitions to HALF_OPEN
        with patch("inferia.common.circuit_breaker.time") as mock_time:
            mock_time.time.return_value = base_time + 10.0
            can = await breaker._can_execute()
            assert can is True
            assert breaker.state == CircuitState.HALF_OPEN


class TestCircuitBreakerRegistry:
    """Registry manages multiple breakers."""

    def test_get_or_create_returns_same_instance(self):
        registry = CircuitBreakerRegistry()
        b1 = registry.get_or_create("redis")
        b2 = registry.get_or_create("redis")
        assert b1 is b2

    def test_get_or_create_different_names(self):
        registry = CircuitBreakerRegistry()
        b1 = registry.get_or_create("redis")
        b2 = registry.get_or_create("postgres")
        assert b1 is not b2

    def test_status_returns_all_breakers(self):
        registry = CircuitBreakerRegistry()
        registry.get_or_create("redis")
        registry.get_or_create("postgres")
        status = registry.status()
        assert "redis" in status
        assert "postgres" in status
        assert status["redis"]["state"] == "closed"

    def test_get_returns_none_for_unknown(self):
        registry = CircuitBreakerRegistry()
        assert registry.get("nonexistent") is None

"""Shared test configuration for inference service tests."""

import pytest

from inferia.common.circuit_breaker import circuit_breaker_registry


@pytest.fixture(autouse=True)
def _clear_circuit_breakers():
    """Reset global circuit breaker state between tests."""
    circuit_breaker_registry._breakers.clear()
    yield
    circuit_breaker_registry._breakers.clear()

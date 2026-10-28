"""Shared test configuration for inference service tests."""

import pytest

from common.circuit_breaker import circuit_breaker_registry


@pytest.fixture(autouse=True)
def _public_dns(monkeypatch):
    """Resolve no upstream name, without the network: nothing to pin, so the
    request goes to the URL as written. Pinning tests set their own answer."""
    async def _resolve(hostname):
        return []

    monkeypatch.setattr("inference.core.validators._resolve", _resolve)


@pytest.fixture(autouse=True)
def _clear_circuit_breakers():
    """Reset global circuit breaker state between tests."""
    circuit_breaker_registry._breakers.clear()
    yield
    circuit_breaker_registry._breakers.clear()

"""Shared test configuration for inference service tests."""

import pytest

from common.circuit_breaker import circuit_breaker_registry


@pytest.fixture(autouse=True)
def _public_dns(monkeypatch):
    """Resolve every upstream name to a public address, without the network."""
    async def _resolve(hostname):
        return ["93.184.215.14"]

    monkeypatch.setattr("inference.core.validators._resolve", _resolve)


@pytest.fixture(autouse=True)
def _clear_circuit_breakers():
    """Reset global circuit breaker state between tests."""
    circuit_breaker_registry._breakers.clear()
    yield
    circuit_breaker_registry._breakers.clear()

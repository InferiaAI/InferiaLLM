"""Unit tests for health_routes.py — verify the /health/services aggregator
probes the correct mounted paths in unified (single-port) mode.

Bug fixed: the old code probed `http://localhost:{settings.port}/health` (the
gateway self-check at the SPA root — always 200 HTML, not a health JSON) and
`http://localhost:8001/health` (the retired inference process port).  In unified
mode all services share APP_PORT; the gateway health is at /api/health and
inference is at /inf/health.
"""
from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from api_gateway.gateway.health_routes import services_health_check


# ---------------------------------------------------------------------------
# Async helpers — monkeypatch env + stub out I/O, capture probe URLs
# ---------------------------------------------------------------------------

async def _capture_service_urls(monkeypatch, app_port: int = 8000) -> list[str]:
    """Call services_health_check with all HTTP and DB/Redis calls stubbed out,
    return the list of URLs that check_service was invoked with."""
    captured: list[str] = []

    async def _fake_check(name: str, url: str, timeout: float = 5.0):
        captured.append(url)
        from api_gateway.gateway.health_routes import ServiceHealth
        return ServiceHealth(name=name, status="online", latency_ms=1.0)

    async def _fake_db():
        from api_gateway.gateway.health_routes import DependencyHealth
        return DependencyHealth(name="PostgreSQL", status="online")

    async def _fake_redis():
        from api_gateway.gateway.health_routes import DependencyHealth
        return DependencyHealth(name="Redis", status="online")

    monkeypatch.setenv("APP_PORT", str(app_port))

    with patch("api_gateway.gateway.health_routes.check_service", side_effect=_fake_check), \
         patch("api_gateway.gateway.health_routes.check_database", side_effect=_fake_db), \
         patch("api_gateway.gateway.health_routes.check_redis", side_effect=_fake_redis):
        await services_health_check()

    return captured


@pytest.mark.asyncio
async def test_health_services_probes_mounted_api_path(monkeypatch):
    """Gateway health probe must use /api/health, not /health (SPA root)."""
    urls = await _capture_service_urls(monkeypatch, app_port=8000)
    assert any(u.endswith("/api/health") for u in urls), (
        f"No /api/health probe found — got: {urls}"
    )


@pytest.mark.asyncio
async def test_health_services_probes_mounted_inf_path(monkeypatch):
    """Inference health probe must use /inf/health, not :8001/health."""
    urls = await _capture_service_urls(monkeypatch, app_port=8000)
    assert any(u.endswith("/inf/health") for u in urls), (
        f"No /inf/health probe found — got: {urls}"
    )


@pytest.mark.asyncio
async def test_health_services_no_retired_8001(monkeypatch):
    """No probe should target the retired inference port 8001."""
    urls = await _capture_service_urls(monkeypatch, app_port=8000)
    assert not any(":8001" in u for u in urls), (
        f"Retired port 8001 found in probe URLs: {urls}"
    )


@pytest.mark.asyncio
async def test_health_services_uses_app_port_env(monkeypatch):
    """APP_PORT env var must control the gateway + inference probe host:port."""
    urls = await _capture_service_urls(monkeypatch, app_port=9123)
    assert any(":9123/api/health" in u for u in urls), (
        f"APP_PORT=9123 not reflected in probe URLs: {urls}"
    )
    assert any(":9123/inf/health" in u for u in urls), (
        f"APP_PORT=9123 not reflected in probe URLs: {urls}"
    )

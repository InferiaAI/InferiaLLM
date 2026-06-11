import pytest
from pathlib import Path
from unittest.mock import AsyncMock
from fastapi import FastAPI
from httpx import AsyncClient, ASGITransport
from services.orchestration.api import admin_aws_discovery as mod


def _app():
    mod.configure(require_permission=lambda perm: (lambda *a, **k: True))
    app = FastAPI()
    app.include_router(mod.router)
    return app


@pytest.mark.asyncio
async def test_regions_ok():
    with mod.__builtins__ if False else __import__("contextlib").nullcontext():
        pass
    app = _app()
    # list_regions is async; patch with AsyncMock so `await list_regions()` works
    with __import__("unittest.mock", fromlist=["patch"]).patch.object(
        mod, "list_regions", new=AsyncMock(return_value=["us-east-1", "us-west-2"])
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.get("/v1/admin/aws/regions")
    assert r.status_code == 200
    assert r.json() == {"regions": ["us-east-1", "us-west-2"], "fallback": False}


@pytest.mark.asyncio
async def test_regions_fallback_on_unavailable():
    from providers.aws.aws_discovery import AwsDiscoveryUnavailable
    from unittest.mock import patch, AsyncMock
    app = _app()
    with patch.object(mod, "list_regions", new=AsyncMock(side_effect=AwsDiscoveryUnavailable("no creds"))):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.get("/v1/admin/aws/regions")
    assert r.status_code == 200
    assert r.json() == {"regions": [], "fallback": True}


@pytest.mark.asyncio
async def test_instance_types_ok():
    from providers.aws.aws_discovery import InstanceTypeInfo
    from unittest.mock import patch, AsyncMock
    info = InstanceTypeInfo("g6.xlarge", 4, 16.0, 1, "L4", True)
    app = _app()
    with patch.object(mod, "list_instance_types", new=AsyncMock(return_value=[info])):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.get("/v1/admin/aws/instance-types?region=us-east-1")
    body = r.json()
    assert body["fallback"] is False
    assert body["instance_types"][0]["instance_type"] == "g6.xlarge"


@pytest.mark.asyncio
async def test_instance_types_fallback_on_unavailable():
    from providers.aws.aws_discovery import AwsDiscoveryUnavailable
    from unittest.mock import patch, AsyncMock
    app = _app()
    with patch.object(mod, "list_instance_types", new=AsyncMock(side_effect=AwsDiscoveryUnavailable("no creds"))):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.get("/v1/admin/aws/instance-types?region=us-east-1")
    assert r.status_code == 200
    assert r.json() == {"instance_types": [], "fallback": True}


@pytest.mark.asyncio
async def test_instance_types_requires_region():
    app = _app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/v1/admin/aws/instance-types")
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_instance_types_empty_region_rejected():
    """region must be min_length=1 — an empty string should 422."""
    app = _app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/v1/admin/aws/instance-types?region=")
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_regions_unconfigured_rbac_returns_503():
    """If configure() was never called, _deps.require_permission is None → 503."""
    app = FastAPI()
    # Reset _deps to simulate unconfigured state
    mod._deps.require_permission = None
    app.include_router(mod.router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/v1/admin/aws/regions")
    assert r.status_code == 503
    # Restore for other tests
    mod.configure(require_permission=lambda perm: (lambda *a, **k: True))


@pytest.mark.asyncio
async def test_instance_types_all_fields_present():
    """to_dict() returns all expected keys."""
    from providers.aws.aws_discovery import InstanceTypeInfo
    from unittest.mock import patch, AsyncMock
    info = InstanceTypeInfo("p4d.24xlarge", 96, 1152.0, 8, "A100", True)
    app = _app()
    with patch.object(mod, "list_instance_types", new=AsyncMock(return_value=[info])):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.get("/v1/admin/aws/instance-types?region=us-east-1")
    body = r.json()
    it = body["instance_types"][0]
    assert it["instance_type"] == "p4d.24xlarge"
    assert it["vcpus"] == 96
    assert it["memory_gb"] == 1152.0
    assert it["gpu_count"] == 8
    assert it["gpu_model"] == "A100"
    assert it["is_gpu"] is True


def test_server_registers_aws_discovery_router():
    """Wire-up guard: server.py must import, configure, and include the router."""
    src = Path(mod.__file__).resolve().parents[1].joinpath("server.py").read_text()
    assert "admin_aws_discovery" in src, "server.py must import the aws-discovery router"
    assert "admin_aws_discovery_api.configure(" in src, "server.py must configure() the router"
    assert "include_router(admin_aws_discovery_api.router)" in src, "server.py must include_router the aws-discovery router"

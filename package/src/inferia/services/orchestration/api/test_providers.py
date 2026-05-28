"""Tests for the providers HTTP endpoints."""
from __future__ import annotations

from fastapi import FastAPI

# Repo-wide version skew: starlette 0.35.1 still passes ``app=`` to
# ``httpx.Client``, which httpx 0.28+ removed. Patch the httpx Client
# constructor to drop the ``app`` kwarg for the duration of this test
# module so the existing sync TestClient-based tests keep working.
import httpx as _httpx
_orig_client_init = _httpx.Client.__init__


def _patched_client_init(self, *args, **kwargs):
    kwargs.pop("app", None)
    return _orig_client_init(self, *args, **kwargs)


_httpx.Client.__init__ = _patched_client_init  # type: ignore[assignment]
from fastapi.testclient import TestClient  # noqa: E402

from inferia.services.orchestration.api.providers import router  # noqa: E402


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    return app


def test_aws_instance_catalog_endpoint_returns_three_classes():
    client = TestClient(_app())
    resp = client.get("/api/v1/providers/aws/instance-catalog")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {"normal_gpu", "heavy_gpu", "cpu"}


def test_aws_instance_catalog_entries_have_required_shape():
    client = TestClient(_app())
    body = client.get("/api/v1/providers/aws/instance-catalog").json()
    sample = body["normal_gpu"][0]
    for key in ("name", "cls", "vcpu", "ram_gb", "gpu_count",
                "gpu_model", "gpu_ram_gb", "price_per_hour"):
        assert key in sample


def test_aws_instance_catalog_cpu_entries_have_zero_gpu():
    client = TestClient(_app())
    body = client.get("/api/v1/providers/aws/instance-catalog").json()
    for it in body["cpu"]:
        assert it["gpu_count"] == 0
        assert it["gpu_model"] is None


def test_aws_instance_catalog_shape_stable_across_calls():
    """The frontend caches via TanStack Query; shape must be deterministic."""
    client = TestClient(_app())
    body1 = client.get("/api/v1/providers/aws/instance-catalog").json()
    body2 = client.get("/api/v1/providers/aws/instance-catalog").json()
    assert body1 == body2

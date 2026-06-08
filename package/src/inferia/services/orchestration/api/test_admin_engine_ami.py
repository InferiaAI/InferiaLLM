from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException

# Repo-wide version skew: starlette 0.35.1 still passes ``app=`` to
# ``httpx.Client``, which httpx 0.28+ removed. Patch the httpx Client
# constructor to drop the ``app`` kwarg so TestClient-based tests keep working.
import httpx as _httpx
_orig_client_init = _httpx.Client.__init__


def _patched_client_init(self, *args, **kwargs):
    kwargs.pop("app", None)
    return _orig_client_init(self, *args, **kwargs)


_httpx.Client.__init__ = _patched_client_init  # type: ignore[assignment]
from fastapi.testclient import TestClient  # noqa: E402

from inferia.services.orchestration.api import admin_engine_ami as mod


def _raise_forbidden():
    raise HTTPException(403, "forbidden")


def _app(*, perm_ok=True, start_bake=None, list_engine_amis=None):
    app = FastAPI()
    mod.configure(
        require_permission=(lambda perm: (lambda *_a, **_k: True)) if perm_ok
        else (lambda perm: (lambda *_a, **_k: _raise_forbidden())),
        start_bake=start_bake or (lambda **kw: "bake-123"),
        list_engine_amis=list_engine_amis or (lambda region: [
            {"ami_id": "ami-1", "vllm_tag": "v0.22.1", "region": region, "created": "2026-06-08"}
        ]),
    )
    app.include_router(mod.router)
    return app


def test_routes_registered_not_404():
    client = TestClient(_app())
    assert client.get("/v1/admin/aws/engine-ami").status_code != 404
    assert client.post("/v1/admin/aws/engine-ami/bake", json={"region": "us-east-1"}).status_code != 404


def test_list_maps_describe_images():
    client = TestClient(_app())
    r = client.get("/v1/admin/aws/engine-ami")
    assert r.status_code == 200
    assert r.json()["amis"][0]["ami_id"] == "ami-1"


def test_bake_starts_task_returns_id():
    client = TestClient(_app())
    r = client.post("/v1/admin/aws/engine-ami/bake", json={"region": "us-east-1"})
    assert r.status_code == 200
    assert r.json()["bake_id"] == "bake-123"
    assert r.json()["status"] == "running"


def test_bake_forbidden_without_perm():
    client = TestClient(_app(perm_ok=False))
    r = client.post("/v1/admin/aws/engine-ami/bake", json={"region": "us-east-1"})
    assert r.status_code == 403


def test_async_lister_awaited():
    async def _alist(region):
        return [{"ami_id": "ami-async", "vllm_tag": "v0.22.1", "region": region, "created": "x"}]
    client = TestClient(_app(list_engine_amis=_alist))
    r = client.get("/v1/admin/aws/engine-ami")
    assert r.status_code == 200 and r.json()["amis"][0]["ami_id"] == "ami-async"


def test_bake_status_unknown_404():
    client = TestClient(_app())
    assert client.get("/v1/admin/aws/engine-ami/bake/nope").status_code == 404


def test_server_registers_engine_ami_router():
    # Wire-up guard (handler unit tests with hand-mounted routers do NOT catch a
    # forgotten include_router/configure in server.py). Assert the source wires it.
    src = Path(mod.__file__).resolve().parents[1].joinpath("server.py").read_text()
    assert "admin_engine_ami" in src, "server.py must import the engine-ami router"
    assert "admin_engine_ami_api.configure(" in src, "server.py must configure() the router"
    assert "include_router(admin_engine_ami_api.router)" in src, "server.py must include_router the engine-ami router"

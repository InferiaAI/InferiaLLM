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

from orchestration.api import admin_engine_ami as mod


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


# --- Fix I2: coverage for production default helpers ---

import types
from unittest.mock import AsyncMock

import providers.aws.engine_ami_bake as _eab
import orchestration.provisioning.engine.aws_orphan_sweep as _sweep
import providers.pulumi.ami as _ami


def test_worker_image_ref(monkeypatch):
    monkeypatch.setenv("INFERIA_WORKER_IMAGE", "ghcr.io/x/worker")
    monkeypatch.setenv("INFERIA_WORKER_IMAGE_TAG", "0.2.5")
    assert mod._worker_image_ref() == "ghcr.io/x/worker:0.2.5"


def test_worker_image_ref_none_without_tag(monkeypatch):
    monkeypatch.delenv("INFERIA_WORKER_IMAGE_TAG", raising=False)
    assert mod._worker_image_ref() is None


def test_ssm_instance_profile(monkeypatch):
    monkeypatch.delenv("INFERIA_BAKE_SSM_INSTANCE_PROFILE", raising=False)
    assert mod._ssm_instance_profile() is None
    monkeypatch.setenv("INFERIA_BAKE_SSM_INSTANCE_PROFILE", "prof")
    assert mod._ssm_instance_profile() == "prof"


@pytest.mark.asyncio
async def test_default_start_bake_records_success(monkeypatch):
    monkeypatch.setattr(_sweep, "resolve_sweep_aws_env",
                        AsyncMock(return_value={"AWS_ACCESS_KEY_ID": "k", "AWS_SECRET_ACCESS_KEY": "s"}))
    monkeypatch.setattr(_eab, "bake_engine_ami",
                        lambda **kw: types.SimpleNamespace(ami_id="ami-z", region=kw["region"]))
    captured = {}
    monkeypatch.setattr(mod.asyncio, "create_task", lambda coro: captured.setdefault("coro", coro))
    bake_id = mod._default_start_bake(region="us-east-1", include_worker_image=False)
    assert mod._BAKES[bake_id]["status"] == "running"
    await captured["coro"]
    assert mod._BAKES[bake_id]["status"] == "succeeded"
    assert mod._BAKES[bake_id]["ami_id"] == "ami-z"
    assert "log" in mod._BAKES[bake_id]  # accumulated log must survive completion


@pytest.mark.asyncio
async def test_default_start_bake_records_failure(monkeypatch):
    monkeypatch.setattr(_sweep, "resolve_sweep_aws_env",
                        AsyncMock(return_value={"AWS_ACCESS_KEY_ID": "k", "AWS_SECRET_ACCESS_KEY": "s"}))
    def _boom(**kw):
        raise RuntimeError("nope")
    monkeypatch.setattr(_eab, "bake_engine_ami", _boom)
    captured = {}
    monkeypatch.setattr(mod.asyncio, "create_task", lambda coro: captured.setdefault("coro", coro))
    bake_id = mod._default_start_bake(region="us-east-1")
    await captured["coro"]
    assert mod._BAKES[bake_id]["status"] == "failed"
    assert "nope" in mod._BAKES[bake_id]["message"]
    assert "log" in mod._BAKES[bake_id]  # accumulated log must survive completion


@pytest.mark.asyncio
async def test_default_list_engine_amis_maps(monkeypatch):
    monkeypatch.setattr(_sweep, "resolve_sweep_aws_env",
                        AsyncMock(return_value={"AWS_ACCESS_KEY_ID": "k", "AWS_SECRET_ACCESS_KEY": "s"}))
    class _FakeEC2:
        def describe_images(self, **kw):
            self.kw = kw
            return {"Images": [{"ImageId": "ami-1", "CreationDate": "2026-06-08",
                                 "Tags": [{"Key": "inferia:vllm-tag", "Value": "v0.22.1"}]}]}
    fake = _FakeEC2()
    monkeypatch.setattr(_ami, "_engine_ec2_client", lambda region, **kw: fake)
    out = await mod._default_list_engine_amis("us-east-1")
    assert out[0]["ami_id"] == "ami-1" and out[0]["vllm_tag"] == "v0.22.1"


@pytest.mark.asyncio
async def test_default_list_engine_amis_no_creds(monkeypatch):
    monkeypatch.setattr(_sweep, "resolve_sweep_aws_env", AsyncMock(return_value=None))
    assert await mod._default_list_engine_amis("us-east-1") == []


def test_list_503_when_not_configured(monkeypatch):
    # Direct misconfiguration: lister unset -> 503.
    from fastapi import FastAPI
    app = FastAPI()
    mod.configure(require_permission=lambda perm: (lambda *_a, **_k: True))
    mod._deps.list_engine_amis = None
    app.include_router(mod.router)
    assert TestClient(app).get("/v1/admin/aws/engine-ami").status_code == 503


# --- Task 5: _BAKES phase + log; _make_progress factory ---

def test_bake_record_accumulates_phase_and_log():
    from orchestration.api import admin_engine_ami as m
    bake_id = "b1"
    m._BAKES[bake_id] = {"status": "running", "phase": "", "ami_id": None, "region": "us-east-1", "log": []}
    cb = m._make_progress(bake_id)
    cb("launching-builder", "launching builder")
    cb("installing-and-pulling", "Pulling fs layer 50%")
    rec = m._BAKES[bake_id]
    assert rec["phase"] == "installing-and-pulling"
    assert rec["log"][-1] == "Pulling fs layer 50%"
    assert rec["log"][0] == "launching builder"


def test_bake_log_caps_at_2000_lines():
    from orchestration.api import admin_engine_ami as m
    bake_id = "b2"
    m._BAKES[bake_id] = {"status": "running", "phase": "", "ami_id": None, "region": "r", "log": []}
    cb = m._make_progress(bake_id)
    for i in range(2100):
        cb("installing-and-pulling", f"line {i}")
    assert len(m._BAKES[bake_id]["log"]) == 2000
    assert m._BAKES[bake_id]["log"][-1] == "line 2099"


def test_make_progress_unknown_bake_id_noop():
    from orchestration.api import admin_engine_ami as m
    cb = m._make_progress("does-not-exist")
    cb("phase", "line")  # must not raise


def test_make_progress_empty_phase_keeps_prior_phase():
    from orchestration.api import admin_engine_ami as m
    m._BAKES["b3"] = {"status": "running", "phase": "creating-ami", "log": [], "region": "r", "ami_id": None}
    cb = m._make_progress("b3")
    cb("", "a log line with no phase")
    assert m._BAKES["b3"]["phase"] == "creating-ami"  # empty phase doesn't overwrite
    assert m._BAKES["b3"]["log"] == ["a log line with no phase"]

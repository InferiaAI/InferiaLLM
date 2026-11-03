"""Extension points wired into the gateway (interfaces.md §6, plan.md §3 rule 2).

For every kind the gateway uses (audit_sink, usage_sink, quota_policy,
token_verifier):

- the OSS default resolves and keeps the pre-extension behaviour;
- a fake selected through INFERIA_EXT_<KIND> is actually called on the real
  code path;
- a bad value stops gateway startup (lifespan), never a silent fallback.

token_verifier is exercised under the REAL ``parent.mount("/api", gateway_app)``
(Mistakes Log 2026-06-13) and in all three AUTH_PROVIDER modes.
"""

import importlib
import sys
import types
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from fastapi import HTTPException

from common import extensions
from common.extensions import ExtensionConfigError
from common.extensions.protocols import (
    AuditEvent,
    AuditSink,
    QuotaContext,
    QuotaDecision,
    QuotaPolicy,
    TokenVerifier,
    UsageEstimate,
    UsageEvent,
    UsageSink,
)

GATEWAY_KINDS = ("audit_sink", "usage_sink", "quota_policy", "token_verifier")
PLUGIN_MOD = "tests_fake_gateway_plugin"


def _ctx(**kw):
    from api_gateway.models import UserContext

    base = dict(
        user_id="u1", username="a@b.com", email="a@b.com", roles=["admin"],
        permissions=["organization:view"], org_id="org1", quota_limit=1, quota_used=0,
    )
    base.update(kw)
    return UserContext(**base)


class FakeAuditSink:
    instances = []

    def __init__(self):
        self.events = []
        FakeAuditSink.instances.append(self)

    async def emit(self, event):
        self.events.append(event)


class RaisingAuditSink:
    async def emit(self, event):
        raise RuntimeError("sink down")


class FakeUsageSink:
    def __init__(self):
        self.calls = []

    async def record(self, events, *, db=None):
        self.calls.append((list(events), db))


class DenyQuota:
    def __init__(self):
        self.calls = []

    async def check(self, ctx, estimate, *, db=None):
        self.calls.append((ctx, estimate, db))
        return QuotaDecision.deny("plugin says no", retry_after_s=7)


class FakeVerifier:
    calls = []

    async def verify(self, token, *, db=None):
        FakeVerifier.calls.append(token)
        if token != "good":
            raise HTTPException(status_code=401, detail="fake: bad token")
        return _ctx(permissions=["fake:perm"])


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    import os

    for key in list(os.environ):
        if key.startswith("INFERIA_EXT_"):
            monkeypatch.delenv(key, raising=False)
    mod = types.ModuleType(PLUGIN_MOD)
    for cls in (FakeAuditSink, RaisingAuditSink, FakeUsageSink, DenyQuota, FakeVerifier):
        setattr(mod, cls.__name__, cls)
    monkeypatch.setitem(sys.modules, PLUGIN_MOD, mod)
    FakeAuditSink.instances.clear()
    FakeVerifier.calls.clear()
    extensions.reset()
    import api_gateway.rbac.middleware as mw

    mw._auth_cache.clear()
    yield
    extensions.reset()
    mw._auth_cache.clear()


# =================================================================== defaults


def test_all_gateway_defaults_resolve_and_satisfy_protocols():
    resolved = extensions.validate(GATEWAY_KINDS)
    assert isinstance(resolved["audit_sink"], AuditSink)
    assert isinstance(resolved["usage_sink"], UsageSink)
    assert isinstance(resolved["quota_policy"], QuotaPolicy)
    assert isinstance(resolved["token_verifier"], TokenVerifier)
    from api_gateway.audit.sinks import PostgresAuditSink
    from api_gateway.policy.extensions import PolicyEngineQuotaPolicy, PolicyEngineUsageSink
    from api_gateway.rbac.token_verifier import DefaultTokenVerifier

    assert type(resolved["audit_sink"]) is PostgresAuditSink
    assert type(resolved["usage_sink"]) is PolicyEngineUsageSink
    assert type(resolved["quota_policy"]) is PolicyEngineQuotaPolicy
    assert type(resolved["token_verifier"]) is DefaultTokenVerifier


# =================================================================== audit_sink


def _audit_create(**kw):
    from api_gateway.models import AuditLogCreate

    base = dict(user_id="u1", org_id="org1", action="deployment.create",
                resource_type="deployment", resource_id="d1",
                details={"k": "v"}, ip_address="10.0.0.1")
    base.update(kw)
    return AuditLogCreate(**base)


@pytest.mark.asyncio
async def test_audit_default_writes_same_row_as_before():
    """PostgresAuditSink writes an AuditLog with exactly the pre-extension fields."""
    from api_gateway.audit import service as svc_mod

    added = []
    session = MagicMock()
    session.add = added.append
    session.commit = AsyncMock()

    @asynccontextmanager
    async def _session():
        yield session

    svc = svc_mod.AuditService()
    ev = svc._to_event(_audit_create())
    with patch("api_gateway.db.database.AsyncSessionLocal", lambda: _session()):
        await extensions.get("audit_sink").emit(ev)

    assert len(added) == 1
    row = added[0]
    assert (row.user_id, row.org_id, row.action, row.category) == (
        "u1", "org1", "deployment.create", svc_mod.derive_category("deployment.create"),
    )
    assert (row.resource_type, row.resource_id, row.details, row.ip_address, row.status) == (
        "deployment", "d1", {"k": "v"}, "10.0.0.1", "success",
    )
    assert row.id == ev.id and row.timestamp == ev.timestamp
    assert row.timestamp.tzinfo is None  # naive UTC column, as before
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_audit_default_swallows_db_errors(caplog):
    from api_gateway.audit.sinks import PostgresAuditSink

    def _boom():
        raise RuntimeError("db down")

    with patch("api_gateway.db.database.AsyncSessionLocal", _boom):
        await PostgresAuditSink().emit(AuditEvent(action="x"))
    assert "Failed to write audit log" in caplog.text


@pytest.mark.asyncio
async def test_audit_log_event_routes_to_env_selected_sink(monkeypatch):
    import asyncio

    from api_gateway.audit.service import AuditService, derive_category

    monkeypatch.setenv("INFERIA_EXT_AUDIT_SINK", f"{PLUGIN_MOD}:FakeAuditSink")
    await AuditService().log_event(AsyncMock(), _audit_create(category=None))
    await asyncio.sleep(0)  # let the fire-and-forget task run
    (sink,) = FakeAuditSink.instances
    (ev,) = sink.events
    assert isinstance(ev, AuditEvent)
    assert ev.action == "deployment.create"
    assert ev.category == derive_category("deployment.create")
    assert ev.ip_address == "10.0.0.1" and ev.details == {"k": "v"}


@pytest.mark.asyncio
async def test_audit_plugin_failure_is_logged_not_raised(monkeypatch, caplog):
    import asyncio

    from api_gateway.audit.service import AuditService

    monkeypatch.setenv("INFERIA_EXT_AUDIT_SINK", f"{PLUGIN_MOD}:RaisingAuditSink")
    await AuditService().log_event(AsyncMock(), _audit_create())
    await asyncio.sleep(0)
    assert "audit sink" in caplog.text.lower() and "sink down" in caplog.text


# =================================================================== quota_policy


@pytest.mark.asyncio
async def test_quota_default_passes_through_policy_engine():
    from api_gateway.gateway.router import QuotaCheckRequest, check_user_quota

    db = AsyncMock()
    with patch("api_gateway.policy.engine.policy_engine") as pe:
        pe.check_quota = AsyncMock(return_value=None)
        resp = await check_user_quota(QuotaCheckRequest(user_id="k1", model="m"), db)
    pe.check_quota.assert_awaited_once_with(db, "k1", "m")
    assert resp == {"status": "ok", "message": "Quota within limits"}


@pytest.mark.asyncio
async def test_quota_default_keeps_429_and_detail():
    from api_gateway.gateway.router import QuotaCheckRequest, check_user_quota

    detail = "Daily quota exceeded (Request Limit). Limit: 1. Used: 1."
    with patch("api_gateway.policy.engine.policy_engine") as pe:
        pe.check_quota = AsyncMock(side_effect=HTTPException(status_code=429, detail=detail))
        with pytest.raises(HTTPException) as exc:
            await check_user_quota(QuotaCheckRequest(user_id="k1", model="m"), AsyncMock())
    assert exc.value.status_code == 429 and exc.value.detail == detail
    assert not (exc.value.headers or {}).get("Retry-After")  # unchanged: no header before


@pytest.mark.asyncio
async def test_quota_default_does_not_swallow_other_errors():
    from api_gateway.policy.extensions import PolicyEngineQuotaPolicy

    with patch("api_gateway.policy.engine.policy_engine") as pe:
        pe.check_quota = AsyncMock(side_effect=HTTPException(status_code=500, detail="x"))
        with pytest.raises(HTTPException) as exc:
            await PolicyEngineQuotaPolicy().check(QuotaContext(user_id="u"), UsageEstimate(), db=None)
    assert exc.value.status_code == 500


@pytest.mark.asyncio
async def test_quota_env_selected_policy_is_called_and_enforced(monkeypatch):
    from api_gateway.gateway.router import QuotaCheckRequest, check_user_quota

    monkeypatch.setenv("INFERIA_EXT_QUOTA_POLICY", f"{PLUGIN_MOD}:DenyQuota")
    db = AsyncMock()
    with patch("api_gateway.policy.engine.policy_engine") as pe:
        pe.check_quota = AsyncMock()
        with pytest.raises(HTTPException) as exc:
            await check_user_quota(QuotaCheckRequest(user_id="k1", model="m"), db)
    pe.check_quota.assert_not_awaited()  # the default is not consulted
    assert exc.value.status_code == 429
    assert exc.value.detail == "plugin says no"
    assert exc.value.headers == {"Retry-After": "7"}
    (ctx, est, seen_db), = extensions.get("quota_policy").calls
    assert ctx == QuotaContext(user_id="k1", model="m") and seen_db is db


# =================================================================== usage_sink


@pytest.mark.asyncio
async def test_usage_default_persists_via_background_task_like_before():
    from api_gateway.gateway.router import UsageTrackRequest, track_user_usage

    usage = {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}
    db, bg = AsyncMock(), MagicMock()
    with patch("api_gateway.gateway.router.policy_engine") as router_pe, patch(
        "api_gateway.policy.engine.policy_engine"
    ) as pe:
        router_pe.increment_redis_only = AsyncMock()
        pe.persist_usage_db = AsyncMock()
        resp = await track_user_usage(UsageTrackRequest(user_id="k1", model="m", usage=usage), bg, db)
        assert resp["status"] == "ok"
        router_pe.increment_redis_only.assert_awaited_once_with(db, "k1", "m", usage)
        pe.persist_usage_db.assert_not_awaited()  # deferred, as before
        call = bg.add_task.call_args
        await call.args[0](*call.args[1:], **call.kwargs)  # run the background task
        pe.persist_usage_db.assert_awaited_once_with(db, "k1", "m", usage)


@pytest.mark.asyncio
async def test_usage_default_records_missing_keys_as_zero():
    from api_gateway.policy.extensions import PolicyEngineUsageSink

    with patch("api_gateway.policy.engine.policy_engine") as pe:
        pe.persist_usage_event_db = AsyncMock(return_value=True)
        await PolicyEngineUsageSink().record(
            [UsageEvent(user_id="a", model="m"), UsageEvent(user_id="b", model="n", total_tokens=4)],
            db="DB",
        )
    assert pe.persist_usage_event_db.await_args_list[0].args[0:1] == ("DB",)
    assert pe.persist_usage_event_db.await_args_list[0].args[2:] == (
        "a", "m", {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    )
    assert pe.persist_usage_event_db.await_args_list[1].args[4]["total_tokens"] == 4


@pytest.mark.asyncio
async def test_usage_sink_preserves_the_stream_event_id_for_replay_deduplication():
    from api_gateway.policy.extensions import PolicyEngineUsageSink

    event = UsageEvent(user_id="a", model="m", event_id="b0d6e9d8-0eb3-4ce7-b5fe-3cd7bea77671")
    with patch("api_gateway.policy.engine.policy_engine") as pe:
        pe.persist_usage_event_db = AsyncMock(return_value=False)
        await PolicyEngineUsageSink().record([event], db="DB")

    assert pe.persist_usage_event_db.await_args.args[1] == event.event_id


@pytest.mark.asyncio
async def test_usage_env_selected_sink_receives_event(monkeypatch):
    from api_gateway.gateway.router import UsageTrackRequest, track_user_usage

    monkeypatch.setenv("INFERIA_EXT_USAGE_SINK", f"{PLUGIN_MOD}:FakeUsageSink")
    db, bg = AsyncMock(), MagicMock()
    usage = {"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7}
    with patch("api_gateway.gateway.router.policy_engine") as router_pe:
        router_pe.increment_redis_only = AsyncMock()
        await track_user_usage(UsageTrackRequest(user_id="k1", model="m", usage=usage), bg, db)
    call = bg.add_task.call_args
    await call.args[0](*call.args[1:], **call.kwargs)
    ((events, seen_db),) = extensions.get("usage_sink").calls
    (ev,) = events
    assert (ev.user_id, ev.model, ev.prompt_tokens, ev.completion_tokens, ev.total_tokens) == (
        "k1", "m", 3, 4, 7,
    )
    assert seen_db is db and ev.schema_version == "1"


# =================================================================== token_verifier


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["local", "oidc", "inferiaauth"])
async def test_default_verifier_dispatches_like_before_in_every_mode(monkeypatch, mode):
    import api_gateway.rbac.middleware as mw
    from api_gateway.rbac.token_verifier import DefaultTokenVerifier

    monkeypatch.setattr(mw.settings, "auth_provider", mode, raising=False)
    local = AsyncMock(side_effect=HTTPException(status_code=401, detail="not local"))
    ext = AsyncMock(return_value=_ctx(user_id="ext"))
    oidc = AsyncMock(return_value=_ctx(user_id="oidc"))
    monkeypatch.setattr(mw, "_resolve_local_token", local)
    monkeypatch.setattr(mw, "_resolve_external_token", ext)
    monkeypatch.setattr(mw, "_resolve_oidc_token", oidc)

    db = object()
    if mode == "local":
        with pytest.raises(HTTPException):
            await DefaultTokenVerifier().verify("t", db=db)
        ext.assert_not_awaited()
        oidc.assert_not_awaited()
    else:
        ctx = await DefaultTokenVerifier().verify("t", db=db)
        assert ctx.user_id == ("ext" if mode == "inferiaauth" else "oidc")
    local.assert_awaited_once_with(db, "t")


@pytest.mark.asyncio
async def test_default_verifier_opens_own_session_when_none_given(monkeypatch):
    import api_gateway.rbac.middleware as mw
    from api_gateway.rbac.token_verifier import DefaultTokenVerifier

    sentinel = object()

    @asynccontextmanager
    async def _session():
        yield sentinel

    monkeypatch.setattr(mw.settings, "auth_provider", "local", raising=False)
    monkeypatch.setattr(mw, "AsyncSessionLocal", lambda: _session())
    local = AsyncMock(return_value=_ctx())
    monkeypatch.setattr(mw, "_resolve_local_token", local)
    await DefaultTokenVerifier().verify("t")
    local.assert_awaited_once_with(sentinel, "t")


@pytest.mark.asyncio
async def test_ws_auth_uses_configured_verifier(monkeypatch):
    pytest.importorskip("websockets")
    from api_gateway.gateway import proxy_routes

    monkeypatch.setenv("INFERIA_EXT_TOKEN_VERIFIER", f"{PLUGIN_MOD}:FakeVerifier")

    @asynccontextmanager
    async def _session():
        yield AsyncMock()

    monkeypatch.setattr(proxy_routes, "AsyncSessionLocal", lambda: _session())
    ctx = await proxy_routes._get_ws_user_context("good")
    assert ctx.permissions == ["fake:perm"] and FakeVerifier.calls == ["good"]


# ---- real mounted unified app (Mistakes Log 2026-06-13) ----------------------


@pytest_asyncio.fixture
async def mounted(monkeypatch):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    monkeypatch.setattr("api_gateway.rbac.initialization.initialize_default_org", AsyncMock())
    monkeypatch.setattr("api_gateway.app._maybe_declare_catalog", AsyncMock())
    from api_gateway.management.config_manager import config_manager

    monkeypatch.setattr(config_manager, "initialize", AsyncMock())
    monkeypatch.setattr(config_manager, "start_polling", lambda *a, **k: None)
    monkeypatch.setattr(config_manager, "stop_polling", lambda *a, **k: None)
    from api_gateway.gateway.http_client import gateway_http_client
    from api_gateway.gateway.rate_limiter import rate_limiter

    monkeypatch.setattr(gateway_http_client, "close_all", AsyncMock())
    monkeypatch.setattr(rate_limiter, "close", AsyncMock())

    @asynccontextmanager
    async def _session():
        yield AsyncMock()

    import api_gateway.rbac.middleware as mw

    monkeypatch.setattr(mw, "AsyncSessionLocal", lambda *a, **k: _session())
    monkeypatch.setattr("api_gateway.app.AsyncSessionLocal", lambda *a, **k: _session(), raising=False)
    monkeypatch.setattr("api_gateway.db.database.AsyncSessionLocal", lambda *a, **k: _session())

    gateway_app = importlib.import_module("api_gateway.app").app

    @asynccontextmanager
    async def run():
        parent = FastAPI()
        parent.mount("/api", gateway_app)
        async with gateway_app.router.lifespan_context(gateway_app):
            async with AsyncClient(
                transport=ASGITransport(app=parent, raise_app_exceptions=False),
                base_url="http://test",
            ) as client:
                yield client

    return run


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["local", "oidc", "inferiaauth"])
async def test_env_verifier_is_used_by_http_middleware_under_mount(monkeypatch, mounted, mode):
    import api_gateway.rbac.middleware as mw

    monkeypatch.setattr(mw.settings, "auth_provider", mode, raising=False)
    monkeypatch.setenv("INFERIA_EXT_TOKEN_VERIFIER", f"{PLUGIN_MOD}:FakeVerifier")
    async with mounted() as client:
        ok = await client.get("/api/auth/permissions", headers={"Authorization": "Bearer good"})
        bad = await client.get("/api/auth/permissions", headers={"Authorization": "Bearer nope"})
        public = await client.get("/api/health")
    assert ok.status_code == 200, ok.text
    assert ok.json()["permissions"] == ["fake:perm"]
    assert bad.status_code == 401 and bad.json()["detail"] == "fake: bad token"
    assert public.status_code != 401  # skip paths still bypass the verifier
    assert FakeVerifier.calls == ["good", "nope"]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["local", "oidc", "inferiaauth"])
async def test_default_verifier_under_mount_keeps_mode_dispatch(monkeypatch, mounted, mode):
    import api_gateway.rbac.middleware as mw

    monkeypatch.setattr(mw.settings, "auth_provider", mode, raising=False)
    monkeypatch.setattr(mw, "_resolve_local_token",
                        AsyncMock(side_effect=HTTPException(status_code=401, detail="Invalid token")))
    monkeypatch.setattr(mw, "_resolve_external_token", AsyncMock(return_value=_ctx(permissions=["ext"])))
    monkeypatch.setattr(mw, "_resolve_oidc_token", AsyncMock(return_value=_ctx(permissions=["oidc"])))
    async with mounted() as client:
        r = await client.get("/api/auth/permissions", headers={"Authorization": "Bearer t"})
    if mode == "local":
        assert r.status_code == 401 and r.json()["detail"] == "Invalid token"
    else:
        assert r.status_code == 200
        assert r.json()["permissions"] == (["ext"] if mode == "inferiaauth" else ["oidc"])


# =================================================================== fail fast


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "var, value",
    [
        ("INFERIA_EXT_AUDIT_SINK", "no_such_module_xyz:Sink"),
        ("INFERIA_EXT_TOKEN_VERIFIER", f"{PLUGIN_MOD}:Nope"),
        ("INFERIA_EXT_QUOTA_POLICY", f"{PLUGIN_MOD}:FakeAuditSink"),  # wrong protocol
        ("INFERIA_EXT_AUDTI_SINK", f"{PLUGIN_MOD}:FakeAuditSink"),  # typo'd kind
    ],
)
async def test_bad_extension_config_stops_gateway_startup(monkeypatch, mounted, var, value):
    import api_gateway.rbac.initialization as init

    seed = AsyncMock()
    monkeypatch.setattr(init, "initialize_default_org", seed)
    monkeypatch.setenv(var, value)
    with pytest.raises(ExtensionConfigError, match=var):
        async with mounted():
            pass
    seed.assert_not_awaited()  # failed before touching the database

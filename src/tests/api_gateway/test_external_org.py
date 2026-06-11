"""Tests for shadow-organization provisioning (rbac/external_org.py).

In external modes the token's org_id is the IdP org UUID with no local row;
ensure_external_org provisions one (name fetched from the IdP with the
caller's token, fallback otherwise) plus a membership row, and never raises.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from api_gateway.rbac.external_org import (
    _fallback_name,
    ensure_external_org,
    fetch_idp_org_name,
)

ORG_ID = "12c566f1-1927-471e-8175-3d9faad2df94"


# ─── fetch_idp_org_name ───────────────────────────────────────────────────────


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_fetch_name_flat_shape(monkeypatch):
    from api_gateway.config import settings

    monkeypatch.setattr(settings, "external_auth_url", "https://idp.test", raising=False)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == f"/api/v1/orgs/{ORG_ID}"
        assert request.headers["Authorization"] == "Bearer tok"
        return httpx.Response(200, json={"id": ORG_ID, "name": "Acme Corp", "slug": "acme"})

    name = await fetch_idp_org_name(ORG_ID, "tok", client=_client(handler))
    assert name == "Acme Corp"


@pytest.mark.asyncio
async def test_fetch_name_nested_org_envelope(monkeypatch):
    from api_gateway.config import settings

    monkeypatch.setattr(settings, "external_auth_url", "https://idp.test", raising=False)
    name = await fetch_idp_org_name(
        ORG_ID, "tok",
        client=_client(lambda r: httpx.Response(200, json={"org": {"name": "Nested Inc"}})),
    )
    assert name == "Nested Inc"


@pytest.mark.asyncio
async def test_fetch_name_failures_return_none(monkeypatch):
    from api_gateway.config import settings

    monkeypatch.setattr(settings, "external_auth_url", "https://idp.test", raising=False)

    # non-2xx
    assert await fetch_idp_org_name(ORG_ID, "tok", client=_client(lambda r: httpx.Response(404))) is None
    # malformed body
    assert await fetch_idp_org_name(
        ORG_ID, "tok", client=_client(lambda r: httpx.Response(200, content=b"not json"))
    ) is None
    # blank name
    assert await fetch_idp_org_name(
        ORG_ID, "tok", client=_client(lambda r: httpx.Response(200, json={"name": "  "}))
    ) is None

    # network error
    def boom(request):
        raise httpx.ConnectError("nope")

    assert await fetch_idp_org_name(ORG_ID, "tok", client=_client(boom)) is None


@pytest.mark.asyncio
async def test_fetch_name_skips_without_token_or_url(monkeypatch):
    from api_gateway.config import settings

    monkeypatch.setattr(settings, "external_auth_url", "https://idp.test", raising=False)
    assert await fetch_idp_org_name(ORG_ID, None) is None
    assert await fetch_idp_org_name(ORG_ID, "") is None

    monkeypatch.setattr(settings, "external_auth_url", None, raising=False)
    assert await fetch_idp_org_name(ORG_ID, "tok") is None


# ─── ensure_external_org ──────────────────────────────────────────────────────


def _result(first):
    m = MagicMock()
    m.scalars.return_value.first.return_value = first
    return m


def _db(execute_results) -> AsyncMock:
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[_result(r) for r in execute_results])
    db.add = MagicMock()
    return db


@pytest.mark.asyncio
async def test_creates_org_with_idp_name():
    db = _db([None, None])  # org missing, membership missing
    with patch(
        "api_gateway.rbac.external_org.fetch_idp_org_name",
        AsyncMock(return_value="Acme Corp"),
    ):
        await ensure_external_org(db, ORG_ID, user_id="u1", bearer_token="tok")

    added = [c.args[0] for c in db.add.call_args_list]
    assert len(added) == 2  # org + membership
    org = added[0]
    assert org.id == ORG_ID
    assert org.name == "Acme Corp"
    membership = added[1]
    assert membership.user_id == "u1"
    assert membership.org_id == ORG_ID
    assert db.commit.await_count == 2


@pytest.mark.asyncio
async def test_falls_back_to_synthetic_name_when_fetch_fails():
    db = _db([None, None])
    with patch(
        "api_gateway.rbac.external_org.fetch_idp_org_name",
        AsyncMock(return_value=None),
    ):
        await ensure_external_org(db, ORG_ID, user_id="u1", bearer_token="tok")

    org = db.add.call_args_list[0].args[0]
    assert org.name == _fallback_name(ORG_ID)
    assert ORG_ID[:8] in org.name


@pytest.mark.asyncio
async def test_existing_org_skips_create_and_fetch():
    existing = MagicMock()
    db = _db([existing, MagicMock()])  # org exists, membership exists
    fetch = AsyncMock(return_value="should-not-be-called")
    with patch(
        "api_gateway.rbac.external_org.fetch_idp_org_name", fetch
    ):
        await ensure_external_org(db, ORG_ID, user_id="u1", bearer_token="tok")

    fetch.assert_not_awaited()
    db.add.assert_not_called()
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_membership_added_when_missing_even_if_org_exists():
    db = _db([MagicMock(), None])  # org exists, membership missing
    await ensure_external_org(db, ORG_ID, user_id="u1", bearer_token="tok")

    assert db.add.call_count == 1
    membership = db.add.call_args.args[0]
    assert membership.user_id == "u1"
    assert membership.role == "member"


@pytest.mark.asyncio
async def test_no_org_id_is_a_noop():
    db = AsyncMock()
    await ensure_external_org(db, None, user_id="u1", bearer_token="tok")
    await ensure_external_org(db, "", user_id="u1", bearer_token="tok")
    db.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_db_errors_never_raise():
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=RuntimeError("db down"))
    # Must swallow — token resolution cannot fail on provisioning hiccups.
    await ensure_external_org(db, ORG_ID, user_id="u1", bearer_token="tok")

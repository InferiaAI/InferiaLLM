"""Login limits that a caller cannot reset by rewording its request.

authenticate_user lowercases the email, so the limiter must key on that form,
and a per-account cap holds even when every attempt comes from a new IP.
"""

from unittest.mock import AsyncMock

import pyotp
import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from api_gateway.db.models import User as DBUser
from api_gateway.models import AuthToken
from api_gateway.rbac import router as r
from common.rate_limit import login_account_limiter, login_rate_limiter


@pytest_asyncio.fixture
async def login_client(monkeypatch):
    monkeypatch.setattr(r.auth_service, "log_failed_login", AsyncMock(return_value=None))
    login_rate_limiter._store.clear()
    login_account_limiter._store.clear()
    r._last_totp_step.clear()
    app = FastAPI()
    app.include_router(r.router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        yield c
    login_rate_limiter._store.clear()
    login_account_limiter._store.clear()


async def _attempt(client, username, **kw):
    return await client.post("/auth/login", json={"username": username, "password": "x", **kw})


@pytest.mark.asyncio
async def test_case_variants_share_one_bucket(login_client, monkeypatch):
    monkeypatch.setattr(r.auth_service, "authenticate_user", AsyncMock(return_value=None))

    for name in ["admin@x.com", "Admin@x.com", "ADMIN@x.com", " admin@x.com", "aDmin@x.com"]:
        await _attempt(login_client, name)
    resp = await _attempt(login_client, "AdMiN@X.CoM")

    assert resp.status_code == 429


@pytest.mark.asyncio
async def test_the_account_cap_holds_across_addresses(login_client, monkeypatch):
    """A faked or shared client IP must not reset the count for one account."""
    monkeypatch.setattr(r.auth_service, "authenticate_user", AsyncMock(return_value=None))
    seen = {"n": 0}

    def _fresh_ip_each_time(username, ip):
        seen["n"] += 1
        return (True, None)

    monkeypatch.setattr(login_rate_limiter, "is_allowed", _fresh_ip_each_time)

    statuses = [(await _attempt(login_client, "victim@x.com")).status_code for _ in range(22)]

    assert statuses[:21] == [401] * 21
    assert statuses[21] == 429


@pytest.mark.asyncio
async def test_successful_logins_do_not_count_against_the_account(login_client, monkeypatch):
    user = DBUser(id="u-ok", email="a@x.com", password_hash="h", totp_enabled=False)
    monkeypatch.setattr(r.auth_service, "authenticate_user", AsyncMock(return_value=user))
    monkeypatch.setattr(r.auth_service, "login", AsyncMock(return_value=AuthToken(
        access_token="a", refresh_token="r", token_type="bearer", expires_in=900)))
    monkeypatch.setattr(login_rate_limiter, "is_allowed", lambda *a: (True, None))

    statuses = [(await _attempt(login_client, "a@x.com")).status_code for _ in range(25)]

    assert set(statuses) == {200}


@pytest.mark.asyncio
async def test_a_2fa_code_cannot_be_used_twice(login_client, monkeypatch):
    secret = pyotp.random_base32()
    user = DBUser(id="u-2fa", email="a@x.com", password_hash="h", totp_enabled=True,
                  totp_secret=secret)
    monkeypatch.setattr(r.auth_service, "authenticate_user", AsyncMock(return_value=user))
    monkeypatch.setattr(r.auth_service, "login", AsyncMock(return_value=AuthToken(
        access_token="a", refresh_token="r", token_type="bearer", expires_in=900)))
    code = pyotp.TOTP(secret).now()

    first = await _attempt(login_client, "a@x.com", totp_code=code)
    replay = await _attempt(login_client, "a@x.com", totp_code=code)
    # pyotp also accepts the code in full-width digits, a different string.
    full_width = "".join(chr(ord(c) + 0xFEE0) for c in code)
    replay_wide = await _attempt(login_client, "a@x.com", totp_code=full_width)

    assert first.status_code == 200
    assert replay.status_code == 401
    assert replay_wide.status_code == 401

"""Tests for scripts.smoke.lib — uses respx to mock all HTTP calls."""
from __future__ import annotations

import httpx
import pytest
import respx

from scripts.smoke.lib import (
    APIError,
    SmokeAPI,
)


BASE = "http://test"


@pytest.fixture
def api() -> SmokeAPI:
    return SmokeAPI(base_url=BASE)


@respx.mock
def test_login_stores_token(api: SmokeAPI) -> None:
    respx.post(f"{BASE}/v1/auth/login").mock(
        return_value=httpx.Response(200, json={"access_token": "tok-123"})
    )
    api.login("admin@example.com", "pw")
    assert api._token == "tok-123"


@respx.mock
def test_login_propagates_4xx(api: SmokeAPI) -> None:
    respx.post(f"{BASE}/v1/auth/login").mock(
        return_value=httpx.Response(401, json={"detail": "bad creds"})
    )
    with pytest.raises(APIError) as exc:
        api.login("admin@example.com", "wrong")
    assert exc.value.status == 401

"""/internal/models must rate-limit each API key on its own.

Every caller reaches it through the inference service, so the peer address is
the same for all of them. Keyed on that, one busy caller throttles everyone.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from api_gateway.gateway import router as gw


def _request(auth=None, host="10.0.0.5"):
    request = MagicMock()
    request.headers = {"Authorization": auth} if auth else {}
    request.client.host = host
    request.state = MagicMock(spec=[])  # no JWT user
    return request


async def _bucket_for(auth, key_record):
    seen = []

    async def is_allowed(key):
        seen.append(key)
        return True, {"limit": 1, "remaining": 1, "reset": 0}

    with patch.object(gw.rate_limiter.limiter, "is_allowed", side_effect=is_allowed), \
         patch.object(gw.settings, "rate_limit_enabled", True), \
         patch.object(gw.policy_engine, "verify_api_key", AsyncMock(return_value=key_record)):
        try:
            await gw.list_models(_request(auth), skip=0, limit=50, db=AsyncMock())
        except HTTPException:
            pass
    return seen


@pytest.mark.asyncio
async def test_two_keys_behind_one_address_get_separate_buckets():
    a = await _bucket_for("Bearer sk-a", MagicMock(id="key-a", org_id=None))
    b = await _bucket_for("Bearer sk-b", MagicMock(id="key-b", org_id=None))

    assert a == ["apikey:key-a"]
    assert b == ["apikey:key-b"]


@pytest.mark.asyncio
async def test_an_invalid_key_is_still_limited_by_address():
    assert await _bucket_for("Bearer sk-bad", None) == ["ip:10.0.0.5"]


@pytest.mark.asyncio
async def test_a_missing_header_is_still_limited_by_address():
    assert await _bucket_for(None, None) == ["ip:10.0.0.5"]


@pytest.mark.asyncio
async def test_an_over_limit_key_gets_429_before_any_lookup_of_models():
    async def refuse(key):
        return False, {"limit": 1, "remaining": 0, "reset": 0}

    db = AsyncMock()
    with patch.object(gw.rate_limiter.limiter, "is_allowed", side_effect=refuse), \
         patch.object(gw.settings, "rate_limit_enabled", True), \
         patch.object(gw.policy_engine, "verify_api_key",
                      AsyncMock(return_value=MagicMock(id="key-a", org_id="org-1"))):
        with pytest.raises(HTTPException) as exc:
            await gw.list_models(_request("Bearer sk-a"), skip=0, limit=50, db=db)

    assert exc.value.status_code == 429
    db.execute.assert_not_called()

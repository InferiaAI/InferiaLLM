"""Tests for auth middleware TTL cache (#70)."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from cachetools import TTLCache

from api_gateway.rbac import middleware as mw


@pytest.fixture(autouse=True)
def clear_cache():
    """Clear the auth cache before and after each test."""
    mw._auth_cache.clear()
    yield
    mw._auth_cache.clear()


class TestAuthCacheProperties:
    def test_cache_is_ttl_cache(self):
        """Auth cache must be a TTLCache with bounded size."""
        assert isinstance(mw._auth_cache, TTLCache)

    def test_cache_maxsize_is_reasonable(self):
        """Cache should hold at least 1000 entries but not be unbounded."""
        assert 1000 <= mw._auth_cache.maxsize <= 100000

    def test_cache_ttl_is_short(self):
        """TTL should be short enough to avoid stale permissions (< 120s)."""
        assert mw._auth_cache.ttl <= 120

    def test_cache_ttl_positive(self):
        assert mw._auth_cache.ttl > 0

    def test_cache_maxsize_positive(self):
        assert mw._auth_cache.maxsize > 0


class TestCacheBehavior:
    def test_different_tokens_get_separate_entries(self):
        """Different tokens produce different cache entries."""
        from api_gateway.models import UserContext

        ctx1 = UserContext(
            user_id="u1", username="a@test.com", email="a@test.com",
            roles=["admin"], permissions=["admin:all"], org_id="org1",
            quota_limit=10000, quota_used=0,
        )
        ctx2 = UserContext(
            user_id="u2", username="b@test.com", email="b@test.com",
            roles=["user"], permissions=[], org_id="org2",
            quota_limit=100, quota_used=0,
        )
        mw._auth_cache["tok_a"] = ctx1
        mw._auth_cache["tok_b"] = ctx2

        assert mw._auth_cache["tok_a"].user_id == "u1"
        assert mw._auth_cache["tok_b"].user_id == "u2"

    def test_cache_evicts_on_maxsize(self):
        """Cache should evict when maxsize is reached."""
        small_cache = TTLCache(maxsize=3, ttl=30)
        small_cache["a"] = 1
        small_cache["b"] = 2
        small_cache["c"] = 3
        small_cache["d"] = 4
        assert len(small_cache) == 3
        assert "d" in small_cache

    def test_cache_hit_returns_stored_value(self):
        """Cached value should be retrievable."""
        from api_gateway.models import UserContext

        ctx = UserContext(
            user_id="u1", username="a@test.com", email="a@test.com",
            roles=["admin"], permissions=["admin:all"], org_id="org1",
            quota_limit=10000, quota_used=0,
        )
        mw._auth_cache["tok_cached"] = ctx

        assert mw._auth_cache.get("tok_cached") is ctx

    def test_cache_miss_returns_none(self):
        """Missing token should return None."""
        assert mw._auth_cache.get("nonexistent") is None

    def test_clear_empties_cache(self):
        """Clearing cache should remove all entries."""
        mw._auth_cache["a"] = 1
        mw._auth_cache["b"] = 2
        mw._auth_cache.clear()
        assert len(mw._auth_cache) == 0


class TestAuthMiddlewareIntegration:
    @pytest.mark.asyncio
    async def test_cached_token_skips_db(self):
        """When a token is in cache, the middleware should skip DB queries."""
        from api_gateway.models import UserContext

        user_ctx = UserContext(
            user_id="u1", username="test@test.com", email="test@test.com",
            roles=["admin"], permissions=["admin:all"], org_id="org1",
            quota_limit=10000, quota_used=0,
        )
        mw._auth_cache["tok_valid"] = user_ctx

        # Build request mock
        request = MagicMock()
        request.url.path = "/api/test"
        request.method = "GET"
        headers = {"Authorization": "Bearer tok_valid", "upgrade": "", "connection": ""}
        request.headers = MagicMock()
        request.headers.get = lambda k, default="": headers.get(k, default)
        request.state = MagicMock(spec=[])

        call_next = AsyncMock(return_value=MagicMock())

        # Should NOT create a DB session
        with patch("api_gateway.rbac.middleware.AsyncSessionLocal") as mock_session:
            await mw.auth_middleware(request, call_next)

        mock_session.assert_not_called()
        assert request.state.user == user_ctx

    @pytest.mark.asyncio
    async def test_new_token_populates_cache(self):
        """A successful auth with a new token should populate the cache."""
        token = "tok_new_abc"
        assert token not in mw._auth_cache

        mock_user = MagicMock()
        mock_user.id = "u1"
        mock_user.email = "test@test.com"

        mock_role = MagicMock()
        mock_role.permissions = ["admin:all"]
        mock_role_result = MagicMock()
        mock_role_result.scalars.return_value.all.return_value = [mock_role]

        mock_db = AsyncMock()
        mock_db.execute.return_value = mock_role_result
        mock_db.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db.__aexit__ = AsyncMock(return_value=None)

        request = MagicMock()
        request.url.path = "/api/test"
        request.method = "GET"
        headers = {"Authorization": f"Bearer {token}", "upgrade": "", "connection": ""}
        request.headers = MagicMock()
        request.headers.get = lambda k, default="": headers.get(k, default)
        request.state = MagicMock(spec=[])

        call_next = AsyncMock(return_value=MagicMock())

        with patch("api_gateway.rbac.middleware.AsyncSessionLocal", return_value=mock_db), \
             patch("api_gateway.rbac.middleware.auth_service") as mock_svc:
            mock_svc.get_current_user = AsyncMock(return_value=(mock_user, "org1", ["admin"]))
            await mw.auth_middleware(request, call_next)

        assert token in mw._auth_cache
        assert mw._auth_cache[token].user_id == "u1"

    @pytest.mark.asyncio
    async def test_failed_auth_not_cached(self):
        """A failed auth should NOT cache anything."""
        from fastapi import HTTPException

        token = "tok_bad"

        mock_db = AsyncMock()
        mock_db.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db.__aexit__ = AsyncMock(return_value=None)

        request = MagicMock()
        request.url.path = "/api/test"
        request.method = "GET"
        headers = {"Authorization": f"Bearer {token}", "upgrade": "", "connection": ""}
        request.headers = MagicMock()
        request.headers.get = lambda k, default="": headers.get(k, default)
        request.state = MagicMock(spec=[])

        call_next = AsyncMock()

        with patch("api_gateway.rbac.middleware.AsyncSessionLocal", return_value=mock_db), \
             patch("api_gateway.rbac.middleware.auth_service") as mock_svc:
            mock_svc.get_current_user = AsyncMock(
                side_effect=HTTPException(status_code=401, detail="Invalid token")
            )
            await mw.auth_middleware(request, call_next)

        assert token not in mw._auth_cache

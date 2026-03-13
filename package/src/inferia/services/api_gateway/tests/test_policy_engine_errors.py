"""Tests for policy engine error handling."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import HTTPException
from redis.exceptions import RedisError


class TestPolicyEngineErrors:
    """Verify policy engine error paths."""

    def _make_engine(self):
        """Create a PolicyEngine with mocked Redis."""
        with patch("inferia.services.api_gateway.policy.engine.redis") as mock_redis:
            mock_redis.from_url.return_value = AsyncMock()
            from inferia.services.api_gateway.policy.engine import PolicyEngine

            return PolicyEngine()

    @pytest.mark.asyncio
    async def test_verify_api_key_empty_returns_none(self):
        engine = self._make_engine()
        result = await engine.verify_api_key(AsyncMock(), "")
        assert result is None

    @pytest.mark.asyncio
    async def test_verify_api_key_no_match_returns_none(self):
        engine = self._make_engine()
        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_db.execute = AsyncMock(return_value=mock_result)

        result = await engine.verify_api_key(mock_db, "sk-testkey12345")
        assert result is None

    @pytest.mark.asyncio
    async def test_quota_redis_unavailable_fails_open(self):
        """Redis failure allows request (fail-open)."""
        engine = self._make_engine()
        engine._check_redis_quota = AsyncMock(
            side_effect=RedisError("Connection refused")
        )

        mock_db = AsyncMock()
        # Should NOT raise — fails open
        await engine.check_quota(mock_db, "user-1", "gpt-4")

    @pytest.mark.asyncio
    async def test_quota_exceeded_raises_429(self):
        engine = self._make_engine()
        # Return usage exceeding defaults (1000 requests)
        engine._check_redis_quota = AsyncMock(return_value=("9999", "0"))

        mock_db = AsyncMock()
        with pytest.raises(HTTPException) as exc:
            await engine.check_quota(mock_db, "user-1", "gpt-4")
        assert exc.value.status_code == 429

    @pytest.mark.asyncio
    async def test_resolve_context_invalid_key_returns_error(self):
        engine = self._make_engine()
        engine.verify_api_key = AsyncMock(return_value=None)

        result = await engine.resolve_context(AsyncMock(), "bad-key", "model")
        assert result["valid"] is False
        assert "Invalid" in result["error"]

    @pytest.mark.asyncio
    async def test_usage_tracking_redis_failure_does_not_raise(self):
        """Usage increment failure is non-blocking."""
        engine = self._make_engine()
        engine._increment_redis_with_breaker = AsyncMock(
            side_effect=RedisError("Connection refused")
        )

        # Should NOT raise
        await engine.increment_redis_only(
            "user-1", "model", {"total_tokens": 100}
        )

    @pytest.mark.asyncio
    async def test_persist_usage_db_failure_does_not_raise(self):
        """DB persistence failure is logged but doesn't crash."""
        engine = self._make_engine()
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(side_effect=Exception("DB connection lost"))

        # Should NOT raise
        await engine.persist_usage_db(
            mock_db, "user-1", "model", {"total_tokens": 100}
        )

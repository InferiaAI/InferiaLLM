"""Tests for rate limiter — security layer."""

import time
import pytest
from unittest.mock import patch, MagicMock

from inferia.common.rate_limit import RateLimiter


class TestRateLimiter:
    """Verify rate limiter enforces request limits correctly."""

    def test_first_request_allowed(self):
        limiter = RateLimiter(max_requests=5, window_seconds=60)
        allowed, retry = limiter.is_allowed("user1", "1.2.3.4")
        assert allowed is True
        assert retry is None

    def test_requests_at_max_allowed(self):
        """The Nth request (at max_requests) should still be allowed since count starts at 0."""
        limiter = RateLimiter(max_requests=3, window_seconds=60)
        for _ in range(3):
            allowed, _ = limiter.is_allowed("user1", "1.2.3.4")
        # 3 requests made, count is now 3 which equals max_requests
        # Next request should be blocked
        assert allowed is True

    def test_exceeding_max_requests_blocked(self):
        limiter = RateLimiter(max_requests=3, window_seconds=60, block_duration_seconds=300)
        for _ in range(3):
            limiter.is_allowed("user1", "1.2.3.4")
        # 4th request exceeds limit
        allowed, retry = limiter.is_allowed("user1", "1.2.3.4")
        assert allowed is False
        assert retry == 300

    def test_blocked_stays_blocked(self):
        limiter = RateLimiter(max_requests=1, window_seconds=60, block_duration_seconds=300)
        limiter.is_allowed("user1", "1.2.3.4")  # count=1
        limiter.is_allowed("user1", "1.2.3.4")  # blocked
        # Still blocked on next attempt
        allowed, retry = limiter.is_allowed("user1", "1.2.3.4")
        assert allowed is False
        assert isinstance(retry, int)
        assert retry > 0

    def test_different_identifiers_tracked_independently(self):
        limiter = RateLimiter(max_requests=1, window_seconds=60)
        limiter.is_allowed("user1", "1.2.3.4")  # count=1
        limiter.is_allowed("user1", "1.2.3.4")  # blocked
        # Different user should still be allowed
        allowed, _ = limiter.is_allowed("user2", "1.2.3.4")
        assert allowed is True

    def test_same_identifier_different_ips_tracked_independently(self):
        limiter = RateLimiter(max_requests=1, window_seconds=60)
        limiter.is_allowed("user1", "1.2.3.4")  # count=1
        limiter.is_allowed("user1", "1.2.3.4")  # blocked for this IP
        # Same user, different IP -> independent tracking
        allowed, _ = limiter.is_allowed("user1", "5.6.7.8")
        assert allowed is True

    def test_cleanup_removes_expired_entries(self):
        limiter = RateLimiter(max_requests=5, window_seconds=1, block_duration_seconds=1)
        limiter.is_allowed("user1", "1.2.3.4")
        assert len(limiter._store) == 1

        # Simulate time passing beyond 2x window
        with patch("inferia.common.rate_limit.time") as mock_time:
            mock_time.time.return_value = time.time() + 10
            limiter.cleanup()

        assert len(limiter._store) == 0

    def test_window_resets_after_expiry(self):
        limiter = RateLimiter(max_requests=1, window_seconds=1)
        limiter.is_allowed("user1", "1.2.3.4")  # count=1

        # Simulate time passing beyond window
        with patch("inferia.common.rate_limit.time") as mock_time:
            mock_time.time.return_value = time.time() + 2
            allowed, _ = limiter.is_allowed("user1", "1.2.3.4")
            assert allowed is True  # Window reset

    def test_retry_after_value_correct(self):
        limiter = RateLimiter(max_requests=1, window_seconds=60, block_duration_seconds=120)
        limiter.is_allowed("user1", "1.2.3.4")  # count=1
        _, retry = limiter.is_allowed("user1", "1.2.3.4")  # blocked
        assert retry == 120

        # On subsequent check, retry_after should be less
        # Simulate 10 seconds passing
        now = time.time()
        limiter._store["user1:1.2.3.4"]["blocked_until"] = now + 120
        with patch("inferia.common.rate_limit.time") as mock_time:
            mock_time.time.return_value = now + 10
            _, retry = limiter.is_allowed("user1", "1.2.3.4")
            assert retry == 110  # 120 - 10

    def test_decorator_raises_429(self):
        """rate_limit_auth decorator raises HTTPException 429."""
        from fastapi import HTTPException
        from inferia.common.rate_limit import rate_limit_auth

        limiter = RateLimiter(max_requests=0, window_seconds=60, block_duration_seconds=60)

        @rate_limit_auth(limiter, identifier_param="username")
        async def dummy_endpoint(request, username="test"):
            return {"ok": True}

        # Build a mock Request
        mock_request = MagicMock()
        mock_request.client.host = "127.0.0.1"

        with pytest.raises(HTTPException) as exc_info:
            import asyncio
            asyncio.get_event_loop().run_until_complete(
                dummy_endpoint(request=mock_request, username="test")
            )
        assert exc_info.value.status_code == 429
        assert "Retry-After" in exc_info.value.headers

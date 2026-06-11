"""Tests for Redis-backed rate limiter — issue #40.

The in-process TokenBucketLimiter stores state in a Python dict per-process.
With workers > 1, rate limits are multiplied by N. The fix is to use Redis
for shared state across workers when available.
"""

import time
import pytest
from unittest.mock import MagicMock, patch

from inference.core.rate_limiter import (
    TokenBucketLimiter,
    RedisTokenBucketLimiter,
    create_rate_limiter,
)


class TestRedisTokenBucketLimiterExists:
    """Verify the Redis-backed rate limiter is available."""

    def test_redis_limiter_class_exists(self):
        """RedisTokenBucketLimiter must exist as an alternative to in-memory."""
        assert RedisTokenBucketLimiter is not None

    def test_redis_limiter_has_check_limit_method(self):
        """Must have the same check_limit(key, rpm, cost) interface."""
        mock_redis = MagicMock()
        limiter = RedisTokenBucketLimiter(mock_redis)
        assert hasattr(limiter, "check_limit")


class TestCreateRateLimiter:
    """Verify the factory picks Redis when available."""

    def test_returns_redis_limiter_when_redis_url_set(self):
        """When REDIS_HOST is configured, should return Redis-backed limiter."""
        with patch(
            "inference.core.rate_limiter.redis"
        ) as mock_redis_mod:
            mock_redis_mod.Redis.return_value = MagicMock()
            limiter = create_rate_limiter(redis_url="redis://localhost:6379/0")
            assert isinstance(limiter, RedisTokenBucketLimiter)

    def test_falls_back_to_in_memory_when_no_redis(self):
        """Without Redis, should fall back to in-memory limiter."""
        limiter = create_rate_limiter(redis_url=None)
        assert isinstance(limiter, TokenBucketLimiter)

    def test_falls_back_to_in_memory_when_redis_unavailable(self):
        """If Redis import fails or connection fails, should fall back."""
        with patch(
            "inference.core.rate_limiter.redis", None
        ):
            limiter = create_rate_limiter(redis_url="redis://localhost:6379/0")
            assert isinstance(limiter, TokenBucketLimiter)


class TestRedisTokenBucketLimiterLogic:
    """Verify the Redis limiter uses Redis for state, not local dict."""

    def test_check_limit_calls_redis_not_local_dict(self):
        """The Redis limiter must use Redis commands, not a local dict."""
        mock_redis = MagicMock()
        # Simulate: pipeline returns [removed_count, added_ok, current_count, expire_ok]
        mock_pipe = MagicMock()
        mock_pipe.execute.return_value = [0, True, 0, True]
        mock_redis.pipeline.return_value.__enter__ = MagicMock(return_value=mock_pipe)
        mock_redis.pipeline.return_value.__exit__ = MagicMock(return_value=False)

        limiter = RedisTokenBucketLimiter(mock_redis)
        allowed, wait_time = limiter.check_limit("test-key", rpm=60)

        assert allowed is True
        assert wait_time == 0.0
        # Must have used redis pipeline, not a local dict
        mock_redis.pipeline.assert_called()

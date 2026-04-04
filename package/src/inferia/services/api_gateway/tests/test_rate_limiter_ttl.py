"""Tests for unbounded dict → TTLCache fix in rate limiters (#72)."""

import pytest
from cachetools import TTLCache
from unittest.mock import MagicMock, patch

from inferia.services.api_gateway.gateway.rate_limiter import (
    InMemoryRateLimiter,
    TokenBucket,
)


class TestInMemoryRateLimiterTTLCache:
    def test_buckets_is_ttl_cache(self):
        """InMemoryRateLimiter.buckets must be a TTLCache, not a plain dict."""
        limiter = InMemoryRateLimiter()
        assert isinstance(limiter.buckets, TTLCache)

    def test_buckets_has_bounded_maxsize(self):
        limiter = InMemoryRateLimiter()
        assert limiter.buckets.maxsize > 0
        assert limiter.buckets.maxsize <= 100000

    def test_buckets_has_positive_ttl(self):
        limiter = InMemoryRateLimiter()
        assert limiter.buckets.ttl > 0

    def test_get_bucket_creates_new_entry(self):
        limiter = InMemoryRateLimiter()
        bucket = limiter._get_bucket("user:123")
        assert isinstance(bucket, TokenBucket)
        assert "user:123" in limiter.buckets

    def test_get_bucket_returns_same_instance(self):
        limiter = InMemoryRateLimiter()
        b1 = limiter._get_bucket("user:123")
        b2 = limiter._get_bucket("user:123")
        assert b1 is b2

    @pytest.mark.asyncio
    async def test_is_allowed_creates_bucket(self):
        limiter = InMemoryRateLimiter()
        allowed, metadata = await limiter.is_allowed("user:456")
        assert allowed is True
        assert "user:456" in limiter.buckets
        assert "limit" in metadata
        assert "remaining" in metadata

    @pytest.mark.asyncio
    async def test_rate_limit_exhaustion(self):
        limiter = InMemoryRateLimiter()
        # Exhaust all tokens
        for _ in range(limiter.burst_size):
            allowed, _ = await limiter.is_allowed("user:exhaust")
        # Next request should be denied
        allowed, metadata = await limiter.is_allowed("user:exhaust")
        assert allowed is False

    def test_eviction_when_maxsize_exceeded(self):
        """TTLCache should evict oldest entries when maxsize is reached."""
        limiter = InMemoryRateLimiter()
        # Override with a small cache for testing
        limiter.buckets = TTLCache(maxsize=3, ttl=120)
        limiter._get_bucket("a")
        limiter._get_bucket("b")
        limiter._get_bucket("c")
        assert len(limiter.buckets) == 3
        limiter._get_bucket("d")
        assert len(limiter.buckets) == 3  # oldest evicted
        assert "d" in limiter.buckets


class TestTokenBucket:
    @pytest.mark.asyncio
    async def test_consume_success(self):
        bucket = TokenBucket(capacity=10, refill_rate=1.0)
        assert await bucket.consume(1) is True

    @pytest.mark.asyncio
    async def test_consume_exact_capacity(self):
        bucket = TokenBucket(capacity=5, refill_rate=0.0)
        for _ in range(5):
            assert await bucket.consume(1) is True
        assert await bucket.consume(1) is False

    @pytest.mark.asyncio
    async def test_consume_more_than_available(self):
        bucket = TokenBucket(capacity=2, refill_rate=0.0)
        assert await bucket.consume(3) is False

    @pytest.mark.asyncio
    async def test_consume_zero_tokens(self):
        bucket = TokenBucket(capacity=5, refill_rate=1.0)
        assert await bucket.consume(0) is True

"""
Rate limiting implementation using token bucket algorithm.
Supports both in-memory and Redis-based rate limiting.
"""

import time
from typing import Dict, Tuple
from fastapi import HTTPException, status, Request
from datetime import datetime, timedelta
import asyncio

from config import settings

try:
    import redis

    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False


class TokenBucket:
    """Thread-safe token bucket algorithm for rate limiting."""

    def __init__(self, capacity: int, refill_rate: float):
        """
        Args:
            capacity: Maximum number of tokens in the bucket
            refill_rate: Tokens added per second
        """
        self.capacity = capacity
        self.tokens = capacity
        self.refill_rate = refill_rate
        self.last_refill = time.time()
        # Use asyncio.Lock for thread-safe operations in async context
        self._lock = asyncio.Lock()

    async def consume(self, tokens: int = 1) -> bool:
        """
        Try to consume tokens from the bucket.
        Returns True if successful, False if insufficient tokens.
        Thread-safe implementation using async lock.
        """
        async with self._lock:
            self._refill()

            if self.tokens >= tokens:
                self.tokens -= tokens
                return True
            return False

    def _refill(self):
        """Refill tokens based on elapsed time."""
        now = time.time()
        elapsed = now - self.last_refill
        self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_rate)
        self.last_refill = now


class InMemoryRateLimiter:
    """In-memory rate limiter using token bucket algorithm."""

    def __init__(self):
        self.buckets: Dict[str, TokenBucket] = {}
        self.requests_per_minute = settings.rate_limit_requests_per_minute
        self.burst_size = settings.rate_limit_burst_size

    def _get_bucket(self, key: str) -> TokenBucket:
        """Get or create token bucket for key."""
        if key not in self.buckets:
            # Convert requests per minute to tokens per second
            refill_rate = self.requests_per_minute / 60.0
            self.buckets[key] = TokenBucket(
                capacity=self.burst_size, refill_rate=refill_rate
            )
        return self.buckets[key]

    async def is_allowed(self, key: str) -> Tuple[bool, Dict[str, any]]:
        """
        Check if request is allowed.
        Returns (is_allowed, metadata)
        Thread-safe implementation using async token bucket.
        """
        bucket = self._get_bucket(key)
        allowed = await bucket.consume(1)

        metadata = {
            "limit": self.requests_per_minute,
            "remaining": int(bucket.tokens),
            "reset": int(time.time() + 60),
        }

        return allowed, metadata


class RedisRateLimiter:
    """Redis-based distributed rate limiter."""

    def __init__(self):
        if not REDIS_AVAILABLE:
            raise ImportError("redis package is required for RedisRateLimiter")

        self.redis_client = redis.from_url(settings.redis_url, decode_responses=True)
        self.requests_per_minute = settings.rate_limit_requests_per_minute
        self.window_seconds = 60

    async def is_allowed(self, key: str) -> Tuple[bool, Dict[str, any]]:
        """
        Check if request is allowed using sliding window algorithm.
        Returns (is_allowed, metadata)
        """
        now = time.time()
        window_start = now - self.window_seconds

        # Redis key for rate limiting
        redis_key = f"rate_limit:{key}"

        # Remove old entries
        self.redis_client.zremrangebyscore(redis_key, 0, window_start)

        # Count requests in current window
        current_count = self.redis_client.zcard(redis_key)

        # Check if allowed
        allowed = current_count < self.requests_per_minute

        if allowed:
            # Add new request timestamp
            self.redis_client.zadd(redis_key, {str(now): now})
            # Set expiry on key
            self.redis_client.expire(redis_key, self.window_seconds * 2)

        metadata = {
            "limit": self.requests_per_minute,
            "remaining": max(0, self.requests_per_minute - current_count - 1),
            "reset": int(now + self.window_seconds),
        }

        return allowed, metadata


import logging

logger = logging.getLogger("rate_limiter")


class RateLimiter:
    """Main rate limiter that chooses between in-memory and Redis implementations."""

    def __init__(self):
        if settings.use_redis_rate_limit and REDIS_AVAILABLE:
            try:
                self.limiter = RedisRateLimiter()
                self.backend = "redis"
            except Exception as e:
                logger.warning(
                    f"Failed to initialize Redis rate limiter: {e}. Falling back to in-memory."
                )
                self.limiter = InMemoryRateLimiter()
                self.backend = "in-memory"
        else:
            self.limiter = InMemoryRateLimiter()
            self.backend = "in-memory"

    async def check_rate_limit(self, request: Request) -> None:
        """
        Check rate limit for request.
        Raises HTTPException if rate limit exceeded.
        """
        if not settings.rate_limit_enabled:
            return

        # Use user_id as key if available, otherwise use IP address
        user_id = getattr(request.state, "user_id", None)
        if user_id:
            key = f"user:{user_id}"
        else:
            # Handle test environment where request.client might be None
            client_host = request.client.host if request.client else "test-client"
            key = f"ip:{client_host}"

        allowed, metadata = await self.limiter.is_allowed(key)

        if not allowed:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Rate limit exceeded. Limit: {metadata['limit']} requests per minute. "
                f"Try again at {datetime.fromtimestamp(metadata['reset']).isoformat()}",
                headers={
                    "X-RateLimit-Limit": str(metadata["limit"]),
                    "X-RateLimit-Remaining": str(metadata["remaining"]),
                    "X-RateLimit-Reset": str(metadata["reset"]),
                    "Retry-After": str(metadata["reset"] - int(time.time())),
                },
            )

        # Add rate limit info to request state
        request.state.rate_limit_metadata = metadata


# Global rate limiter instance
rate_limiter = RateLimiter()

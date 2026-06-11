"""
Rate limiting implementation using token bucket algorithm.
Supports both in-memory and Redis-based rate limiting.
"""

import time
from typing import Any, Dict, Tuple
from fastapi import HTTPException, status, Request
from datetime import datetime
import asyncio
from cachetools import TTLCache

from services.api_gateway.config import settings

try:
    import redis.asyncio as redis

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
        self.buckets: TTLCache = TTLCache(maxsize=10000, ttl=120)
        self.requests_per_minute = settings.rate_limit_requests_per_minute
        self.burst_size = settings.rate_limit_burst_size

    def _get_bucket(self, key: str) -> TokenBucket:
        """Get or create token bucket for key."""
        # Use dict.setdefault() for atomic check-and-set (GIL-safe).
        # Prevents a race where concurrent coroutines both see the key
        # as missing and create separate TokenBucket instances.
        refill_rate = self.requests_per_minute / 60.0
        return self.buckets.setdefault(
            key, TokenBucket(capacity=self.burst_size, refill_rate=refill_rate)
        )

    async def is_allowed(self, key: str) -> Tuple[bool, Dict[str, Any]]:
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

        from services.api_gateway.gateway.redis_pool import get_redis_client
        self.redis_client = get_redis_client()
        self.requests_per_minute = settings.rate_limit_requests_per_minute
        self.window_seconds = 60

    async def is_allowed(self, key: str) -> Tuple[bool, Dict[str, Any]]:
        """
        Check if request is allowed using sliding window algorithm.
        Uses a Redis pipeline to batch all commands in a single round-trip.
        Returns (is_allowed, metadata)
        """
        now = time.time()
        window_start = now - self.window_seconds
        redis_key = f"rate_limit:{key}"

        # Single pipeline: clean old entries, add new, count, set expiry
        async with self.redis_client.pipeline(transaction=True) as pipe:
            pipe.zremrangebyscore(redis_key, 0, window_start)
            pipe.zadd(redis_key, {str(now): now})
            pipe.zcard(redis_key)
            pipe.expire(redis_key, self.window_seconds * 2)
            results = await pipe.execute()

        # results: [removed_count, added_count, current_count, expire_ok]
        current_count = results[2]
        allowed = current_count <= self.requests_per_minute

        if not allowed:
            # Over limit — remove the optimistic add
            await self.redis_client.zrem(redis_key, str(now))

        metadata = {
            "limit": self.requests_per_minute,
            "remaining": max(0, self.requests_per_minute - current_count),
            "reset": int(now + self.window_seconds),
        }

        return allowed, metadata

    async def close(self):
        await self.redis_client.aclose()


import logging
import os

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

        if self.backend == "in-memory":
            workers = int(os.getenv("WEB_CONCURRENCY", "1"))
            if workers > 1:
                logger.warning(
                    "In-memory rate limiter is active with %d workers. "
                    "Rate limits will NOT be enforced across workers. "
                    "Set use_redis_rate_limit=true or WEB_CONCURRENCY=1.",
                    workers,
                )

    async def check_rate_limit(self, request: Request) -> None:
        """
        Check rate limit for request.
        Raises HTTPException if rate limit exceeded.
        """
        if not settings.rate_limit_enabled:
            return

        # Use user_id from authenticated JWT context, not from headers
        user = getattr(request.state, "user", None)
        user_id = user.user_id if user else None
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

    async def close(self):
        close_method = getattr(self.limiter, "close", None)
        if close_method is None:
            return
        try:
            await close_method()
        except Exception as e:
            logger.warning(f"Failed to close rate limiter backend: {e}")


# Global rate limiter instance
rate_limiter = RateLimiter()


import time
import logging
import os
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)

try:
    import redis
except ImportError:
    redis = None


class TokenBucketLimiter:
    """
    In-Memory Token Bucket Rate Limiter.
    Thread-safe enough for async (single process).
    For multi-process, use RedisTokenBucketLimiter.
    """
    def __init__(self):
        # Key -> (tokens, last_refill_timestamp)
        self.buckets: Dict[str, Tuple[float, float]] = {}

    def check_limit(self, key: str, rpm: int, cost: int = 1) -> Tuple[bool, float]:
        """
        Check if request is allowed.
        Returns (is_allowed, wait_time_seconds).
        wait_time_seconds is 0.0 if allowed.
        """
        if rpm <= 0:
            return True, 0.0 # No limit

        now = time.time()
        bucket = self.buckets.get(key)

        # Max tokens = RPM (simple burst policy)
        # Refill rate = RPM / 60.0 tokens per second
        max_tokens = float(rpm)
        refill_rate = rpm / 60.0

        if not bucket:
            # First request: Full bucket minus cost
            self.buckets[key] = (max_tokens - cost, now)
            return True, 0.0

        tokens, last_refill = bucket

        # Refill tokens
        time_passed = now - last_refill
        refill_amount = time_passed * refill_rate
        tokens = min(max_tokens, tokens + refill_amount)

        if tokens >= cost:
            # Allowed
            self.buckets[key] = (tokens - cost, now)
            return True, 0.0
        else:
            # Denied
            needed = cost - tokens
            wait_time = needed / refill_rate if refill_rate > 0 else 60.0
            return False, wait_time


class RedisTokenBucketLimiter:
    """
    Redis-backed Token Bucket Rate Limiter.
    Shares state across multiple uvicorn workers via Redis sorted sets.
    Uses a sliding window counter (same approach as api_gateway RedisRateLimiter).
    """
    def __init__(self, redis_client):
        self.redis_client = redis_client

    def check_limit(self, key: str, rpm: int, cost: int = 1) -> Tuple[bool, float]:
        """
        Check if request is allowed using sliding window in Redis.
        Returns (is_allowed, wait_time_seconds).
        """
        if rpm <= 0:
            return True, 0.0

        now = time.time()
        window_seconds = 60
        window_start = now - window_seconds
        redis_key = f"inference_rl:{key}"

        with self.redis_client.pipeline() as pipe:
            pipe.zremrangebyscore(redis_key, 0, window_start)
            pipe.zadd(redis_key, {str(now): now})
            pipe.zcard(redis_key)
            pipe.expire(redis_key, window_seconds * 2)
            results = pipe.execute()

        current_count = results[2]

        if current_count <= rpm:
            return True, 0.0
        else:
            # Remove the optimistic add
            self.redis_client.zrem(redis_key, str(now))
            wait_time = window_seconds / rpm if rpm > 0 else 60.0
            return False, wait_time


def create_rate_limiter(redis_url: Optional[str] = None):
    """
    Factory: returns Redis-backed limiter if Redis is available, else in-memory.
    """
    if redis_url and redis is not None:
        try:
            client = redis.Redis.from_url(redis_url, decode_responses=True)
            client.ping()
            logger.info("Using Redis-backed rate limiter (shared across workers)")
            return RedisTokenBucketLimiter(client)
        except Exception as e:
            logger.warning(
                "Redis unavailable (%s), falling back to in-memory rate limiter. "
                "Rate limits will NOT be shared across workers.",
                e,
            )
    return TokenBucketLimiter()


# Module-level singleton — used by orchestrator.py
_redis_url = os.environ.get("REDIS_URL") or os.environ.get("REDIS_HOST")
if _redis_url and not _redis_url.startswith("redis://"):
    _redis_url = f"redis://{_redis_url}:6379/0"

rate_limiter = create_rate_limiter(redis_url=_redis_url)

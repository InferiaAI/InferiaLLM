"""
Shared async Redis connection pool for the API gateway.

All gateway components (rate limiter, policy engine, health checks)
should use ``get_redis_pool()`` instead of creating independent clients.
"""

import logging
from typing import Optional

import redis.asyncio as redis

from inferia.services.api_gateway.config import settings

logger = logging.getLogger(__name__)

_pool: Optional[redis.ConnectionPool] = None


def get_redis_pool() -> redis.ConnectionPool:
    """Return the shared async Redis connection pool (lazy-init, singleton)."""
    global _pool
    if _pool is None:
        _pool = redis.ConnectionPool.from_url(
            settings.resolved_redis_url,
            decode_responses=True,
            max_connections=int(getattr(settings, "redis_max_connections", 20)),
        )
        logger.info("Shared Redis connection pool created (max_connections=%d)", _pool.max_connections)
    return _pool


def get_redis_client() -> redis.Redis:
    """Return a Redis client backed by the shared pool."""
    return redis.Redis(connection_pool=get_redis_pool())


async def close_redis_pool() -> None:
    """Shutdown the shared pool (call from lifespan shutdown)."""
    global _pool
    if _pool is not None:
        await _pool.disconnect()
        _pool = None

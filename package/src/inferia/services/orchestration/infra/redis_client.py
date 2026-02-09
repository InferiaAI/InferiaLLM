import os
import logging
import redis.asyncio as redis

logger = logging.getLogger(__name__)

_redis = None


def _get_redis_config():
    """Get Redis configuration from environment variables."""
    redis_host = os.getenv("REDIS_HOST", "localhost")
    redis_port = int(os.getenv("REDIS_PORT", "6379"))
    redis_username = os.getenv("REDIS_USERNAME", "default")
    redis_password = os.getenv("REDIS_PASSWORD")
    redis_db = os.getenv("REDIS_DB", "0")
    redis_ssl = os.getenv("REDIS_SSL", "false").lower() == "true"

    config = {
        "host": redis_host,
        "port": redis_port,
        "decode_responses": True,
        "username": redis_username,
        "password": redis_password,
        "db": redis_db,
        "ssl": redis_ssl,
    }

    return config


async def get_redis():
    global _redis
    if _redis is None:
        config = _get_redis_config()
        logger.info(f"Connecting to Redis at {config['host']}:{config['port']}")
        _redis = redis.Redis(**config)
    return _redis


async def close_redis():
    global _redis
    if _redis is not None:
        await _redis.close()
        _redis = None

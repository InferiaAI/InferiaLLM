import os
import redis.asyncio as redis

# REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")

_redis = None

async def get_redis():
    global _redis
    if _redis is None:
        _redis = redis.Redis(
            host='redis-13703.crce217.ap-south-1-1.ec2.cloud.redislabs.com',
            port=13703,
            decode_responses=True,
            username="default",
            password="ZYgHaEncdbMZNRnp3dHEJPe9H7wmsgEk",
        )
    return _redis

async def close_redis():
    global _redis
    if _redis is not None:
        await _redis.close()
        _redis = None

"""Durable, non-blocking usage-event publishing for the inference path."""

import json
import logging
import uuid

import redis.asyncio as redis

from inference.config import settings

logger = logging.getLogger(__name__)
STREAM = "inferia:usage:v1"


async def publish_usage_event(user_id: str, model: str, usage: dict) -> bool:
    """Append one event to Redis Streams. Returns False on transient failure.

    Callers retain the legacy HTTP usage call as a fallback when Redis is not
    available; a successful append is durable until the gateway consumer ACKs
    it.
    """
    client = redis.Redis(host=settings.redis_host, port=settings.redis_port, decode_responses=True)
    try:
        await client.xadd(STREAM, {"data": json.dumps({
            "event_id": str(uuid.uuid4()), "user_id": user_id,
            "model": model, "usage": usage,
        })})
        return True
    except Exception:
        logger.exception("Failed to publish usage event to Redis Stream")
        return False
    finally:
        await client.aclose()

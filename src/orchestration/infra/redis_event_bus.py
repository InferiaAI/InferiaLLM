import logging
import json
import redis.asyncio as redis
from dotenv import load_dotenv
import os

load_dotenv()

log = logging.getLogger(__name__)


class RedisEventBus:
    def __init__(self):
        pool = redis.ConnectionPool(
            host=os.getenv("REDIS_HOST", "localhost"),
            port=int(os.getenv("REDIS_PORT") or 6379),
            username=os.getenv("REDIS_USERNAME", "default"),
            password=os.getenv("REDIS_PASSWORD", ""),
            decode_responses=True,
            max_connections=int(os.getenv("REDIS_EVENT_BUS_POOL_SIZE", "20")),
            socket_timeout=None,
        )
        self.redis = redis.Redis(connection_pool=pool)

    async def close(self):
        """Close the Redis connection."""
        await self.redis.close()

    # -------------------------------------------------
    # PRODUCER
    # -------------------------------------------------
    async def publish(self, stream: str, event: dict):
        await self.redis.xadd(
            stream,
            {"data": json.dumps(event)},
        )

    async def consume(
        self,
        stream: str,
        group: str,
        consumer: str,
        block_ms: int = 5000,
    ):
        try:
            await self.redis.xgroup_create(stream, group, id="0", mkstream=True)
        except redis.ResponseError:
            pass

        while True:
            messages = await self.redis.xreadgroup(
                groupname=group,
                consumername=consumer,
                streams={stream: ">"},
                count=1,
                block=block_ms,
            )

            for _, entries in messages:
                for msg_id, fields in entries:
                    yield msg_id, json.loads(fields["data"])

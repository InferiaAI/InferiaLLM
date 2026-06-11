import asyncio
import redis.asyncio as redis

REDIS_URL = "redis://localhost:6379"
NODE_ID = "19903e08-2203-4561-bb40-6627cd4c64e6"


async def test_liveness():
    r = redis.from_url(REDIS_URL, decode_responses=True)

    key = f"node:{NODE_ID}"

    print("Waiting for TTL expiry...")
    await asyncio.sleep(20)

    exists = await r.exists(key)

    assert not exists, "Node should be offline"
    print("Node correctly expired (OFFLINE)")


if __name__ == "__main__":
    asyncio.run(test_liveness())

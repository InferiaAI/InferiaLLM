"""Redis Streams consumer for inference usage events."""

import asyncio
import json
import logging
from typing import Optional

import redis.asyncio as redis

from api_gateway.gateway.redis_pool import get_redis_client

logger = logging.getLogger(__name__)
STREAM = "inferia:usage:v1"
GROUP = "api-gateway-usage"
CONSUMER = "gateway-1"


async def run_usage_worker(stop: asyncio.Event) -> None:
    """Consume, persist, then ACK batches; unacked entries are redeliverable."""
    client = get_redis_client()
    try:
        try:
            await client.xgroup_create(STREAM, GROUP, id="0", mkstream=True)
        except redis.ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise
        pending = True
        while not stop.is_set():
            # Drain entries previously delivered to this stable consumer before
            # accepting new ones. A process crash before ACK therefore retries
            # the same event on restart (at-least-once delivery).
            rows = await client.xreadgroup(
                GROUP, CONSUMER, {STREAM: "0" if pending else ">"}, count=100,
                block=500 if not pending else None,
            )
            if pending and not rows:
                pending = False
                continue
            for _, entries in rows:
                events = []
                ids = []
                for message_id, fields in entries:
                    try:
                        event = json.loads(fields["data"])
                        if not isinstance(event.get("usage"), dict):
                            raise ValueError("usage is missing")
                        events.append(event)
                        ids.append(message_id)
                    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                        logger.exception("Discarding malformed usage-stream entry %s", message_id)
                        await client.xack(STREAM, GROUP, message_id)
                if not events:
                    continue
                from api_gateway.db.database import AsyncSessionLocal
                from api_gateway.gateway.router import UsageTrackBatchRequest, UsageTrackRequest, track_usage_batch
                from fastapi import BackgroundTasks
                async with AsyncSessionLocal() as db:
                    # Run the sink before ACKing; a crash leaves entries pending.
                    tasks = BackgroundTasks()
                    payload = UsageTrackBatchRequest(events=[UsageTrackRequest(**event) for event in events])
                    await track_usage_batch(payload, tasks, db)
                    for task in tasks.tasks:
                        await task.func(*task.args, **task.kwargs)
                await client.xack(STREAM, GROUP, *ids)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("Usage stream worker stopped after an unexpected error")

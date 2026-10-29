"""Shared test configuration for orchestration service tests."""
from __future__ import annotations

import os

import asyncpg
import grpc
import pytest_asyncio

_DSN = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql://inferia:inferia@localhost:5432/inferia_test",
)


@pytest_asyncio.fixture
async def db_pool():
    """Real asyncpg pool on the test database (schema from scripts/load_test_schema.py)."""
    pool = await asyncpg.create_pool(dsn=_DSN, min_size=1, max_size=4)
    yield pool
    await pool.close()


@pytest_asyncio.fixture
async def grpc_channel():
    """Serve the given (add_fn, servicer) pairs on an ephemeral port; return a channel to them."""
    servers, channels = [], []

    async def _start(*registrations):
        server = grpc.aio.server()
        for add_to_server, servicer in registrations:
            add_to_server(servicer, server)
        port = server.add_insecure_port("127.0.0.1:0")
        await server.start()
        channel = grpc.aio.insecure_channel(f"127.0.0.1:{port}")
        servers.append(server)
        channels.append(channel)
        return channel

    yield _start
    for channel in channels:
        await channel.close()
    for server in servers:
        await server.stop(grace=None)

"""Fixtures for model_cache repository tests."""
from __future__ import annotations

import os

import asyncpg
import pytest_asyncio

_DSN = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql://inferia:inferia@localhost:5432/inferia_test",
)

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS public.model_cache (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    source       text NOT NULL,
    model_id     text NOT NULL,
    revision     text NOT NULL DEFAULT 'main',
    engine_hint  text,
    status       text NOT NULL DEFAULT 'pending',
    bytes_total  bigint NOT NULL DEFAULT 0,
    bytes_done   bigint NOT NULL DEFAULT 0,
    error        text,
    last_used_at timestamptz NOT NULL DEFAULT now(),
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT model_cache_uniq UNIQUE (source, model_id, revision)
);
CREATE INDEX IF NOT EXISTS idx_model_cache_lru ON public.model_cache (last_used_at ASC);
"""


@pytest_asyncio.fixture
async def db_pool():
    """Real asyncpg pool connected to the test database.

    Ensures the model_cache table exists (idempotent DDL), then truncates it so
    each test run starts with a clean slate.
    """
    pool = await asyncpg.create_pool(dsn=_DSN, min_size=1, max_size=4)
    async with pool.acquire() as conn:
        await conn.execute(_CREATE_TABLE_SQL)
        await conn.execute("TRUNCATE model_cache")
    yield pool
    await pool.close()

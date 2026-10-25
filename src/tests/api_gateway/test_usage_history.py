"""Usage history: daily usage per API key and model, scoped to the caller's org.

The checks on the query itself need Postgres and skip without TEST_DATABASE_URL.
"""
import os
import uuid
from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from api_gateway.management import configuration
from api_gateway.schemas.auth import PermissionEnum

pytestmark = pytest.mark.asyncio


async def _call(org_id="org-a", **params):
    """Returns (require_permission mock, result or the HTTPException raised)."""
    ctx = SimpleNamespace(org_id=org_id)
    require = MagicMock()
    with patch.object(configuration, "get_current_user_context", return_value=ctx), \
            patch.object(configuration.authz_service, "require_permission", require):
        try:
            return require, await configuration.get_usage_history(MagicMock(), db=AsyncMock(), **params)
        except HTTPException as e:
            return require, e


async def test_it_requires_org_view():
    require, _ = await _call(start=date(2026, 10, 1), end=date(2026, 10, 2))
    assert require.call_args.args[1] == PermissionEnum.ORG_VIEW


async def test_a_user_without_an_org_gets_nothing():
    _, result = await _call(org_id=None)
    assert result == []


async def test_start_after_end_is_rejected():
    _, result = await _call(start=date(2026, 10, 2), end=date(2026, 10, 1))
    assert isinstance(result, HTTPException) and result.status_code == 400


async def test_a_range_longer_than_the_limit_is_rejected():
    end = date(2026, 10, 25)
    _, result = await _call(start=end - timedelta(days=configuration.MAX_HISTORY_DAYS), end=end)
    assert isinstance(result, HTTPException) and result.status_code == 400


_DSN = os.getenv("TEST_DATABASE_URL")


@pytest.mark.skipif(not _DSN, reason="needs TEST_DATABASE_URL")
async def test_history_is_per_key_and_model_in_the_callers_org_only():
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

    engine = create_async_engine(_DSN.replace("postgresql://", "postgresql+asyncpg://"))
    org, other = f"org-{uuid.uuid4()}", f"org-{uuid.uuid4()}"
    live, revoked, foreign = (str(uuid.uuid4()) for _ in range(3))
    day = date(2026, 10, 20)

    rows = [
        (f"apikey:{live}", day, "qwen", 3, 30, 10),
        (f"apikey:{live}", day, "llama", 1, 5, 5),
        (f"apikey:{revoked}", day, "qwen", 2, 20, 2),
        (f"apikey:{foreign}", day, "qwen", 9, 99, 9),  # another org
        (f"apikey:{live}", day - timedelta(days=40), "qwen", 7, 7, 7),  # outside the range
        ("some-user-id", day, "qwen", 4, 4, 4),  # sandbox usage, not an API key
    ]
    try:
        async with engine.begin() as conn:
            for key_id, owner, active in ((live, org, True), (revoked, org, False), (foreign, other, True)):
                await conn.execute(
                    text("INSERT INTO api_keys (id, name, key_hash, prefix, org_id, is_active) "
                         "VALUES (:id, :name, :hash, 'sk-', :org, :active)"),
                    {"id": key_id, "name": f"key-{key_id[:4]}", "hash": key_id, "org": owner, "active": active},
                )
            for user_id, d, model, n, p, c in rows:
                await conn.execute(
                    text("INSERT INTO usage_stats (id, user_id, date, model, request_count, "
                         "prompt_tokens, completion_tokens, total_tokens) "
                         "VALUES (:id, :u, :d, :m, :n, :p, :c, :t)"),
                    {"id": str(uuid.uuid4()), "u": user_id, "d": d, "m": model,
                     "n": n, "p": p, "c": c, "t": p + c},
                )

        ctx = SimpleNamespace(org_id=org)
        async with AsyncSession(engine) as db:
            with patch.object(configuration, "get_current_user_context", return_value=ctx), \
                    patch.object(configuration.authz_service, "require_permission"):
                result = await configuration.get_usage_history(
                    MagicMock(), start=day - timedelta(days=7), end=day, db=db,
                )

        got = {(r.key_id, r.model): (r.requests, r.total_tokens, r.key_active) for r in result}
        assert got == {
            (live, "qwen"): (3, 40, True),
            (live, "llama"): (1, 10, True),
            (revoked, "qwen"): (2, 22, False),
        }
        assert all(r.day == day for r in result)
    finally:
        async with engine.begin() as conn:
            await conn.execute(text("DELETE FROM usage_stats WHERE user_id = ANY(:u)"),
                               {"u": [r[0] for r in rows]})
            await conn.execute(text("DELETE FROM api_keys WHERE id = ANY(:k)"),
                               {"k": [live, revoked, foreign]})
        await engine.dispose()

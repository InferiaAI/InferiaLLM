"""Tests for ``start_reconciler``: the advisory-lock-guarded entry point
that boots the ProvisioningReconciler on inferia-app startup.

These tests do NOT touch Postgres or the real reconciler graph. They
exercise the advisory-lock decision tree by faking the asyncpg pool +
connection so the only thing under test is the locking + shutdown logic
in ``server.start_reconciler``.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest


def test_reconciler_lock_key_fits_signed_bigint():
    """pg_try_advisory_lock takes a signed bigint. RECONCILER_LOCK_KEY must
    fit in [-(2^63), 2^63-1] or asyncpg fails to encode it and the advisory
    lock call raises — silently crashing the reconciler before it ever
    claims a job (regression: the previous 0xD1F2... value overflowed)."""
    from inferia.services.orchestration.server import RECONCILER_LOCK_KEY

    assert -(2 ** 63) <= RECONCILER_LOCK_KEY <= (2 ** 63) - 1


@pytest.mark.asyncio
async def test_reconciler_starts_on_app_startup_and_holds_advisory_lock():
    """Starting the orchestration app starts a ProvisioningReconciler task
    that holds the Postgres advisory lock."""
    from inferia.services.orchestration.server import start_reconciler

    # Fake db that tracks advisory_lock + advisory_unlock calls.
    lock_calls = []
    conn = MagicMock()
    conn.fetchval = AsyncMock(side_effect=lambda sql, *args:
        (lock_calls.append((sql, args)) or True)
        if "pg_try_advisory_lock" in sql else
        (lock_calls.append((sql, args)) or None)
    )
    db = MagicMock()
    db.acquire = MagicMock()
    db.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    db.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

    stop = asyncio.Event()
    async def runner():
        await start_reconciler(db, handlers={}, emit_event=AsyncMock(),
                               stop_event=stop, lease_holder="t")
    task = asyncio.create_task(runner())
    await asyncio.sleep(0.1)
    stop.set()
    await task

    # First call should be the lock attempt.
    assert any("pg_try_advisory_lock" in s for s, _ in lock_calls)
    # Last call should be the unlock.
    assert any("pg_advisory_unlock" in s for s, _ in lock_calls)


@pytest.mark.asyncio
async def test_reconciler_polls_for_lock_when_not_acquired():
    """If another inferia-app holds the lock, this instance sleeps and
    retries until either it gets the lock or stop fires."""
    from inferia.services.orchestration.server import start_reconciler

    attempts = []
    conn = MagicMock()
    async def _fetchval(sql, *args):
        if "pg_try_advisory_lock" in sql:
            attempts.append(1)
            return False  # never acquired
        return None
    conn.fetchval = _fetchval
    db = MagicMock()
    db.acquire = MagicMock()
    db.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    db.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

    stop = asyncio.Event()
    async def stop_after():
        await asyncio.sleep(0.05)
        stop.set()
    await asyncio.gather(
        start_reconciler(db, handlers={}, emit_event=AsyncMock(),
                         stop_event=stop, lease_holder="t",
                         poll_for_lock_s=0.01),
        stop_after(),
    )
    assert len(attempts) >= 2  # retried at least once

"""Tests for the events.emit_event helper."""
from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from inferia.services.orchestration.services.provisioning.events import emit_event
from inferia.services.orchestration.services.provisioning.jobs.model import Phase


def _make_db_with_conn(conn):
    db = MagicMock()
    db.acquire = MagicMock()
    db.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    db.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    return db


@pytest.mark.asyncio
async def test_emit_event_writes_to_node_provisioning_events():
    conn = MagicMock()
    conn.execute = AsyncMock(return_value="INSERT 0 1")
    db = _make_db_with_conn(conn)
    await emit_event(
        db,
        pool_id=uuid.uuid4(),
        node_id=uuid.uuid4(),
        phase=Phase.PROVISIONING,
        status="log",
        message="Creating EC2 instance",
        extra={"step": 3},
    )
    conn.execute.assert_awaited_once()
    sql = conn.execute.await_args.args[0]
    assert "INSERT INTO node_provisioning_events" in sql


@pytest.mark.asyncio
async def test_emit_event_jsonb_serialises_extra():
    """The `extra` dict is serialised as JSON for the jsonb column."""
    conn = MagicMock()
    conn.execute = AsyncMock(return_value="INSERT 0 1")
    db = _make_db_with_conn(conn)
    await emit_event(
        db,
        pool_id=uuid.uuid4(), node_id=uuid.uuid4(),
        phase=Phase.PREFLIGHT, status="failed",
        message="bad creds",
        extra={"code": "INVALID_CREDENTIALS"},
    )
    args = conn.execute.await_args.args
    last_arg = args[-1]
    parsed = json.loads(last_arg) if isinstance(last_arg, str) else last_arg
    assert parsed == {"code": "INVALID_CREDENTIALS"}


@pytest.mark.asyncio
async def test_emit_event_handles_none_extra():
    conn = MagicMock()
    conn.execute = AsyncMock(return_value="INSERT 0 1")
    db = _make_db_with_conn(conn)
    await emit_event(
        db,
        pool_id=uuid.uuid4(), node_id=uuid.uuid4(),
        phase=Phase.READY, status="succeeded",
        message="node ready",
    )
    conn.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_emit_event_uses_phase_value_string():
    """The phase column is text; we pass the .value string."""
    conn = MagicMock()
    conn.execute = AsyncMock(return_value="INSERT 0 1")
    db = _make_db_with_conn(conn)
    await emit_event(
        db,
        pool_id=uuid.uuid4(), node_id=uuid.uuid4(),
        phase=Phase.BOOTSTRAPPING, status="running",
        message="waiting for worker",
    )
    args = conn.execute.await_args.args
    assert "bootstrapping" in args

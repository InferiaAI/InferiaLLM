"""Single helper for writing rows into node_provisioning_events.

All phase handlers and the reconciler funnel event writes through this
function so the read-side (GET /provisioning, GET /provisioning-logs)
sees a consistent shape. Existing direct-write call sites in
pulumi_aws_adapter.py get removed in Task 10.

The table schema (see
``infra/schema/migrations/20260525_add_node_provisioning_events.sql``)
exposes these columns::

    id         BIGSERIAL PRIMARY KEY
    pool_id    UUID        NOT NULL
    node_id    UUID
    phase      TEXT        NOT NULL
    status     TEXT        NOT NULL
    message    TEXT
    extra      JSONB       NOT NULL DEFAULT '{}'::jsonb
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()

We deliberately do NOT pass ``id`` (server-side ``BIGSERIAL``) or
``created_at`` (server-side ``DEFAULT now()``). Letting Postgres own
both keeps the cursor monotonic across reconciler restarts and avoids
clock-skew bugs on the read path.
"""
from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from orchestration.provisioning_state_machine.jobs.model import (
    EventStatus,
    Phase,
)


async def emit_event(
    db,
    *,
    pool_id: UUID,
    node_id: UUID | None,
    phase: Phase,
    status: EventStatus,
    message: str,
    extra: dict[str, Any] | None = None,
) -> None:
    """Append a row to ``node_provisioning_events``.

    Parameters
    ----------
    db:
        An async-pool object exposing ``async with db.acquire() as conn``
        where ``conn.execute(sql, *args)`` matches the asyncpg contract.
    pool_id:
        Pool the event belongs to (required; the dashboard cursor query
        is ``WHERE pool_id = $1 AND id > $2``).
    node_id:
        Node the event is about, or ``None`` for pool-scoped events
        (e.g. preflight credential checks before a node row exists).
    phase:
        Provisioning phase the event was emitted from. Stored as the
        enum's ``.value`` string so the column stays plain ``TEXT``.
    status:
        One of ``"running" | "succeeded" | "failed" | "log"``.
        ``"log"`` rows are streamed to the dashboard as-is but excluded
        from ``summarize_phases`` (see ``NodeProvisioningRepo``).
    message:
        Human-readable line shown in the dashboard.
    extra:
        Optional structured metadata serialised into the ``jsonb``
        column. ``None`` becomes ``{}`` so the column never holds NULL
        and consumers can do ``row["extra"].get(...)`` safely.
    """
    extra_json = json.dumps(extra if extra is not None else {})
    async with db.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO node_provisioning_events
                (pool_id, node_id, phase, status, message, extra)
            VALUES ($1, $2, $3, $4, $5, $6::jsonb)
            """,
            pool_id,
            node_id,
            phase.value,
            status,
            message,
            extra_json,
        )

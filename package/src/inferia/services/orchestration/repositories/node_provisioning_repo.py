"""Append-only event log for node provisioning UX.

One row per phase status transition plus one row per `log`-status entry
emitted by Pulumi's on_event callback and the cloud-init console poller.
The dashboard polls with a `?after=<id>` cursor; that's the only read
path other than the phase summary used by the Overview tab.
"""
from __future__ import annotations
from typing import Optional, Sequence
from uuid import UUID


PHASES: tuple[str, ...] = (
    "prepare",
    "ami_lookup",
    "pulumi_init",
    "pulumi_up",
    "ec2_running",
    "cloud_init",
    "worker_bootstrap",
    "ready",
)


class NodeProvisioningRepo:
    def __init__(self, db):
        self.db = db

    async def append_event(
        self,
        *,
        pool_id: UUID,
        phase: str,
        status: str,
        message: Optional[str] = None,
        node_id: Optional[UUID] = None,
    ) -> int:
        return await self.db.fetchval(
            """
            INSERT INTO node_provisioning_events
                (pool_id, node_id, phase, status, message)
            VALUES ($1, $2, $3, $4, $5)
            RETURNING id
            """,
            pool_id,
            node_id,
            phase,
            status,
            message,
        )

    async def list_events_after(
        self, *, pool_id: UUID, after_id: int, limit: int = 500,
    ) -> Sequence[dict]:
        rows = await self.db.fetch(
            """
            SELECT id, pool_id, node_id, phase, status, message, created_at
            FROM node_provisioning_events
            WHERE pool_id = $1 AND id > $2
            ORDER BY id
            LIMIT $3
            """,
            pool_id,
            after_id,
            limit,
        )
        return [dict(r) for r in rows]

    async def summarize_phases(self, *, pool_id: UUID) -> Sequence[dict]:
        """Latest non-log row per phase, with started_at = first row time.

        Returned shape per phase:
            {phase, status, started_at, ended_at, last_message}
        ended_at is set only when status in ('succeeded','failed').
        """
        rows = await self.db.fetch(
            """
            SELECT DISTINCT ON (phase)
                phase,
                status,
                message AS last_message,
                CASE WHEN status IN ('succeeded', 'failed') THEN created_at
                     ELSE NULL
                END AS ended_at,
                (SELECT MIN(created_at)
                   FROM node_provisioning_events e2
                  WHERE e2.pool_id = $1 AND e2.phase = e1.phase
                ) AS started_at
            FROM node_provisioning_events e1
            WHERE pool_id = $1 AND status <> 'log'
            ORDER BY phase, id DESC
            """,
            pool_id,
        )
        return [dict(r) for r in rows]

    async def current_phase(self, *, pool_id: UUID) -> Optional[str]:
        summary = await self.summarize_phases(pool_id=pool_id)
        for r in summary:
            if r["status"] == "running":
                return r["phase"]
        return None

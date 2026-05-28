"""Async Postgres repository for the provisioning_jobs queue.

The repository is the only module that writes the jobs table. Phase
handlers MUST NOT touch this table directly — they return PhaseResult
(or raise), and the reconciler calls the repo's transition_to /
schedule_retry / fail methods to durably record the outcome.

All multi-statement operations are single round-trips; we deliberately
do NOT open explicit transactions inside the repo because the reconciler
relies on each method being individually durable (so a crash between
two repo calls doesn't leave a half-written outcome — the lease will
expire and another reconciler will resume from the last-committed state).
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any
from uuid import UUID

from inferia.services.orchestration.services.provisioning.jobs.model import (
    ClassifiedError, Phase, ProvisioningJob,
)


class ProvisioningJobRepository:
    """Wraps a database pool. The `db` argument must expose `.acquire()`
    as an async context manager that yields an asyncpg connection — this
    matches the existing repository pattern in the orchestration service.
    """

    def __init__(self, db):
        self.db = db

    # ---- write paths ----------------------------------------------------

    async def enqueue(
        self,
        *,
        node_id: UUID,
        pool_id: UUID,
        org_id: str,
        provider: str,
        spec: dict[str, Any],
    ) -> UUID:
        """Insert a new pending job, return its id."""
        job_id = uuid.uuid4()
        async with self.db.acquire() as conn:
            await conn.fetchval(
                """
                INSERT INTO provisioning_jobs (
                    id, node_id, pool_id, org_id, provider, spec,
                    phase, attempt_count, created_at, updated_at
                )
                VALUES ($1, $2, $3, $4, $5, $6::jsonb, 'pending', 0, now(), now())
                RETURNING id
                """,
                job_id, node_id, pool_id, org_id, provider, json.dumps(spec),
            )
        return job_id

    async def claim_next_job(
        self, *, lease_holder: str, lease_seconds: int = 300,
    ) -> ProvisioningJob | None:
        """Atomically claim the highest-priority claimable job.

        Returns None if no job is currently claimable (queue empty, all
        leased, or all backed-off). Uses FOR UPDATE SKIP LOCKED so
        concurrent claimers don't contend.
        """
        async with self.db.acquire() as conn:
            row = await conn.fetchrow(
                """
                UPDATE provisioning_jobs
                SET lease_holder = $1,
                    lease_expires_at = now() + make_interval(secs => $2),
                    updated_at = now()
                WHERE id = (
                    SELECT id FROM provisioning_jobs
                    WHERE phase IN ('pending','preflight','provisioning',
                                    'bootstrapping','cancelling')
                      AND (lease_expires_at IS NULL OR lease_expires_at < now())
                      AND (next_attempt_after IS NULL OR next_attempt_after <= now())
                    ORDER BY (phase = 'cancelling') DESC, updated_at ASC
                    LIMIT 1
                    FOR UPDATE SKIP LOCKED
                )
                RETURNING *
                """,
                lease_holder, lease_seconds,
            )
        return ProvisioningJob.from_row(row) if row else None

    async def renew_lease(
        self, *, job_id: UUID, lease_holder: str, lease_seconds: int = 300,
    ) -> bool:
        """Extend the lease deadline. Returns False if the lease was stolen
        (different holder) — caller should cancel the in-flight handler.
        """
        async with self.db.acquire() as conn:
            res = await conn.execute(
                """
                UPDATE provisioning_jobs
                SET lease_expires_at = now() + make_interval(secs => $3),
                    updated_at = now()
                WHERE id = $1 AND lease_holder = $2
                """,
                job_id, lease_holder, lease_seconds,
            )
        return res.endswith(" 1")

    async def release_lease(
        self, *, job_id: UUID, lease_holder: str,
    ) -> None:
        """Clear the lease only if we still hold it. No-op otherwise (defensive)."""
        async with self.db.acquire() as conn:
            await conn.execute(
                """
                UPDATE provisioning_jobs
                SET lease_holder = NULL, lease_expires_at = NULL, updated_at = now()
                WHERE id = $1 AND lease_holder = $2
                """,
                job_id, lease_holder,
            )

    async def transition_to(
        self,
        *,
        job_id: UUID,
        current_phase: Phase,
        next_phase: Phase,
        lease_holder: str,
        outputs: dict[str, Any] | None = None,
    ) -> None:
        """Advance the job to next_phase, optionally merging outputs.
        Phase guard prevents clobbering a concurrent transition."""
        async with self.db.acquire() as conn:
            await conn.execute(
                """
                UPDATE provisioning_jobs
                SET phase = $3::text,
                    pulumi_stack_outputs = COALESCE(pulumi_stack_outputs, '{}'::jsonb)
                                           || COALESCE($4::jsonb, '{}'::jsonb),
                    last_error_code = NULL,
                    last_error_message = NULL,
                    last_error_hint = NULL,
                    error_class = NULL,
                    next_attempt_after = NULL,
                    updated_at = now()
                WHERE id = $1 AND phase = $2::text AND lease_holder = $5
                """,
                job_id, current_phase.value, next_phase.value,
                json.dumps(outputs) if outputs else None,
                lease_holder,
            )

    async def schedule_retry(
        self,
        *,
        job_id: UUID,
        current_phase: Phase,
        lease_holder: str,
        next_attempt_after: datetime,
        attempt_count: int,
        error: ClassifiedError,
    ) -> None:
        """Keep the job in current_phase but bump attempt_count, set
        next_attempt_after, record the error fields, and CLEAR the lease
        so a future reconciler tick can pick it up after the backoff."""
        async with self.db.acquire() as conn:
            await conn.execute(
                """
                UPDATE provisioning_jobs
                SET attempt_count = $3,
                    next_attempt_after = $4,
                    last_error_code = $5,
                    last_error_message = $6,
                    last_error_hint = $7,
                    error_class = $8,
                    lease_holder = NULL,
                    lease_expires_at = NULL,
                    updated_at = now()
                WHERE id = $1 AND phase = $2::text
                """,
                job_id, current_phase.value, attempt_count, next_attempt_after,
                error.code, error.message, error.hint, error.error_class.value,
            )

    async def fail(
        self,
        *,
        job_id: UUID,
        current_phase: Phase,
        lease_holder: str,
        error: ClassifiedError,
    ) -> None:
        """Transition to terminal 'failed' and record the error fields.
        Lease guard ensures we don't overwrite a concurrent transition."""
        async with self.db.acquire() as conn:
            await conn.execute(
                """
                UPDATE provisioning_jobs
                SET phase = 'failed',
                    last_error_code = $3,
                    last_error_message = $4,
                    last_error_hint = $5,
                    error_class = $6,
                    lease_holder = NULL,
                    lease_expires_at = NULL,
                    next_attempt_after = NULL,
                    updated_at = now()
                WHERE id = $1 AND phase = $2::text AND lease_holder = $7
                """,
                job_id, current_phase.value,
                error.code, error.message, error.hint, error.error_class.value,
                lease_holder,
            )

    async def request_cancel(self, *, node_id: UUID) -> bool:
        """Mark the (non-terminal) job for cancellation. Returns False if
        the job is already terminal or there's no job for this node."""
        async with self.db.acquire() as conn:
            res = await conn.execute(
                """
                UPDATE provisioning_jobs
                SET phase = 'cancelling',
                    next_attempt_after = NULL,
                    lease_holder = NULL,
                    lease_expires_at = NULL,
                    updated_at = now()
                WHERE node_id = $1
                  AND phase IN ('pending','preflight','provisioning','bootstrapping')
                """,
                node_id,
            )
        return res.endswith(" 1")

    async def reset_for_retry(self, *, node_id: UUID) -> ProvisioningJob | None:
        """Re-enqueue a failed job: phase='pending', attempt_count=0, all
        error fields cleared. Returns the updated job, or None if the
        current job for this node isn't in 'failed'."""
        async with self.db.acquire() as conn:
            row = await conn.fetchrow(
                """
                UPDATE provisioning_jobs
                SET phase = 'pending',
                    attempt_count = 0,
                    next_attempt_after = NULL,
                    last_error_code = NULL,
                    last_error_message = NULL,
                    last_error_hint = NULL,
                    error_class = NULL,
                    lease_holder = NULL,
                    lease_expires_at = NULL,
                    updated_at = now()
                WHERE node_id = $1 AND phase = 'failed'
                RETURNING *
                """,
                node_id,
            )
        return ProvisioningJob.from_row(row) if row else None

    # ---- read paths -----------------------------------------------------

    async def get(self, job_id: UUID) -> ProvisioningJob | None:
        async with self.db.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM provisioning_jobs WHERE id = $1", job_id,
            )
        return ProvisioningJob.from_row(row) if row else None

    async def get_by_node(self, node_id: UUID) -> ProvisioningJob | None:
        """Most-recent job for a node. Used by HTTP GET /provisioning and
        by the upgrade migration's idempotency check."""
        async with self.db.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT * FROM provisioning_jobs
                WHERE node_id = $1
                ORDER BY created_at DESC
                LIMIT 1
                """,
                node_id,
            )
        return ProvisioningJob.from_row(row) if row else None

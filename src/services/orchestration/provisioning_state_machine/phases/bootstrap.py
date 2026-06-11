"""BootstrapHandler — polls compute_inventory.state until the worker
registers and transitions to 'ready'. Times out as TransientError so
the reconciler retries the whole bootstrap phase (idempotent: each
re-entry just polls again)."""
from __future__ import annotations

import asyncio
from typing import Any

from services.orchestration.provisioning_state_machine.errors import (
    PermanentError, TransientError,
)
from services.orchestration.provisioning_state_machine.jobs.model import (
    Phase, PhaseResult, ProvisioningJob,
)
from services.orchestration.provisioning_state_machine.phases.base import (
    PhaseContext,
)


class BootstrapHandler:
    """Phase: BOOTSTRAPPING."""

    name = Phase.BOOTSTRAPPING

    def __init__(self, *, inventory_repo: Any, poll_interval_s: float = 5.0):
        self.inventory_repo = inventory_repo
        self.poll_interval_s = poll_interval_s

    async def run(self, job: ProvisioningJob, ctx: PhaseContext) -> PhaseResult:
        await ctx.emit_event(
            pool_id=job.pool_id, node_id=job.node_id, phase=Phase.BOOTSTRAPPING,
            status="running",
            message="Waiting for worker on EC2 instance to register",
        )

        deadline = ctx.now().timestamp() + ctx.bootstrap_timeout_s
        while ctx.now().timestamp() < deadline:
            row = await self.inventory_repo.get_node(node_id=job.node_id)
            state = (row or {}).get("state")
            if state == "ready":
                await ctx.emit_event(
                    pool_id=job.pool_id, node_id=job.node_id,
                    phase=Phase.BOOTSTRAPPING, status="succeeded",
                    message="Worker registered as ready",
                )
                # Emit a terminal "ready" row so the dashboard timeline shows
                # the final phase as completed (the READY phase has no handler
                # of its own to emit this).
                await ctx.emit_event(
                    pool_id=job.pool_id, node_id=job.node_id,
                    phase=Phase.READY, status="succeeded",
                    message="Node ready",
                )
                return PhaseResult(next_phase=Phase.READY)
            if state == "failed":
                raise PermanentError(
                    "Worker bootstrap failed (inventory.state=failed)",
                    code="BOOTSTRAP_FAILED",
                    hint="Check the cloud-init logs on the EC2 instance for "
                         "the underlying error (Logs sub-tab).",
                )
            await asyncio.sleep(self.poll_interval_s)

        raise TransientError(
            f"Worker did not register within {ctx.bootstrap_timeout_s}s",
            code="BOOTSTRAP_TIMEOUT",
        )

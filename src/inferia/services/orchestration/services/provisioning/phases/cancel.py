"""CancelHandler — runs pulumi destroy for the node's stack."""
from __future__ import annotations

import asyncio
import logging

from inferia.services.orchestration.services.adapter_engine.adapters.pulumi.programs import (
    build_program,
)
from inferia.services.orchestration.services.adapter_engine.adapters.pulumi.pulumi_aws_adapter import (
    run_pulumi_destroy_sync,
)
from inferia.services.orchestration.services.provisioning.jobs.model import (
    Phase, PhaseResult, ProvisioningJob,
)
from inferia.services.orchestration.services.provisioning.phases.base import (
    PhaseContext, stack_name_for_job,
)

logger = logging.getLogger(__name__)


class CancelHandler:
    """Phase: CANCELLING. Idempotent pulumi destroy.

    Picked up by the reconciler when a user deletes a node that's still
    in flight (or already in a terminal failed state) — the claim query
    orders 'cancelling' jobs ahead of fresh work so user deletes happen
    promptly. ``run_pulumi_destroy_sync`` treats a missing stack as
    success so re-runs (lease loss, crash recovery) are safe.
    """

    name = Phase.CANCELLING

    async def run(self, job: ProvisioningJob, ctx: PhaseContext) -> PhaseResult:
        stack_name = stack_name_for_job(job)
        program = build_program(
            spec=job.spec, stack_outputs=job.pulumi_stack_outputs or {},
        )

        # Pin Pulumi to the SAME persistent local file backend the
        # PulumiUpHandler used to create the stack. Without this, destroy
        # opens a different (cloud / fresh-temp) backend, finds no stack, and
        # run_pulumi_destroy_sync treats "missing stack" as success — silently
        # LEAKING the real EC2. state_dir + PULUMI_BACKEND_URL must match
        # pulumi_up.py exactly.
        import os as _os
        from inferia.services.orchestration.config import settings
        state_dir = settings.pulumi_state_dir
        try:
            _os.makedirs(state_dir, exist_ok=True)
        except OSError:
            pass
        env = dict(ctx.pulumi_env or {})
        env.setdefault("PULUMI_BACKEND_URL", f"file://{state_dir}")
        env.setdefault("PULUMI_CONFIG_PASSPHRASE", settings.pulumi_passphrase or "")

        await ctx.emit_event(
            pool_id=job.pool_id, node_id=job.node_id, phase=Phase.CANCELLING,
            status="running", message=f"Destroying stack {stack_name}",
        )
        # ``run_pulumi_destroy_sync`` already swallows the idempotent
        # "missing stack" case internally (returns None) — so any exception
        # that escapes here is a REAL destroy failure. The node's EC2 may
        # still be running; we must NOT let the reconciler advance to
        # TERMINATED (and purge the row) while the instance keeps billing.
        # Stamp ``metadata.destroy_failed`` so the dashboard surfaces the
        # teardown failure, log loudly, then re-raise so the reconciler's
        # classifier keeps the job retryable instead of marking it done.
        try:
            await asyncio.to_thread(
                run_pulumi_destroy_sync,
                stack_name=stack_name, program=program, env=env,
                state_dir=state_dir,
            )
        except Exception as e:
            reason = f"{type(e).__name__}: {e}"
            logger.error(
                "pulumi destroy FAILED for node=%s stack=%s; EC2 may still be "
                "running — keeping job retryable: %s",
                job.node_id, stack_name, reason,
            )
            await self._record_destroy_failure(job, ctx, reason)
            await ctx.emit_event(
                pool_id=job.pool_id, node_id=job.node_id,
                phase=Phase.CANCELLING, status="log",
                message=f"Stack destroy failed; will retry: {reason}"[:500],
                extra={"destroy_failed": True},
            )
            raise
        await ctx.emit_event(
            pool_id=job.pool_id, node_id=job.node_id, phase=Phase.CANCELLING,
            status="succeeded", message="Stack destroyed",
        )
        return PhaseResult(next_phase=Phase.TERMINATED)

    async def _record_destroy_failure(
        self, job: ProvisioningJob, ctx: PhaseContext, reason: str,
    ) -> None:
        """Stamp metadata.destroy_failed on the node's inventory row.

        Best-effort: a failure to record the flag must not mask the original
        destroy exception (which the caller re-raises). Builds an
        InventoryRepository from ``ctx.db`` because the handler is only
        handed the provisioning-jobs repo, not the inventory repo.
        """
        try:
            from inferia.services.orchestration.repositories.inventory_repo import (
                InventoryRepository,
            )
            await InventoryRepository(ctx.db).mark_destroy_failed(
                job.node_id, reason,
            )
        except Exception:
            logger.exception(
                "failed to record destroy_failed metadata for node=%s; "
                "the destroy error is still surfaced via the job",
                job.node_id,
            )

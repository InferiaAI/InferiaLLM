"""ProvisioningReconciler — claims jobs, dispatches to phase handlers,
records outcomes via the repository.

Single entry point: `await rec.run()` blocks forever (until cancelled).
`tick_once()` exists for tests to drive one iteration synchronously.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from inferia.services.orchestration.services.provisioning.errors import (
    ProvisioningError,
)
from inferia.services.orchestration.services.provisioning.jobs.model import (
    ClassifiedError, ErrorClass, Phase, PhaseResult, ProvisioningJob,
)
from inferia.services.orchestration.services.provisioning.phases.base import (
    PhaseContext, PhaseHandler,
)
from inferia.services.orchestration.services.provisioning.reconciler.concurrency import (
    WorkerPool,
)
from inferia.services.orchestration.services.provisioning.reconciler.lease import (
    renew_loop,
)
from inferia.services.orchestration.services.provisioning.retry.backoff import (
    TRANSIENT_MAX_ATTEMPTS, next_attempt_after,
)
from inferia.services.orchestration.services.provisioning.retry.classifier import (
    classify_error,
)


logger = logging.getLogger(__name__)


class ProvisioningReconciler:
    """The heart of the state machine."""

    def __init__(
        self,
        *,
        repo: Any,
        handlers: dict[Phase, PhaseHandler],
        emit_event: Callable[..., Awaitable[None]],
        db: Any,
        concurrency: int = 4,
        poll_interval_s: float = 2.0,
        lease_seconds: int = 300,
        renew_interval_s: float = 60.0,
        lease_holder: str = "inferia-app",
        load_aws_context: Callable[[ProvisioningJob], Awaitable[tuple[Any, dict[str, str]]]] | None = None,
    ):
        self.repo = repo
        self.handlers = handlers
        self.emit_event = emit_event
        self.db = db
        self.concurrency = concurrency
        self.poll_interval_s = poll_interval_s
        self.lease_seconds = lease_seconds
        self.renew_interval_s = renew_interval_s
        self.lease_holder = lease_holder
        self.load_aws_context = load_aws_context
        self._pool: WorkerPool | None = None

    async def run(self) -> None:
        """Run until cancelled. Starts a WorkerPool of `concurrency`
        coroutines all calling `_one_iteration`."""
        self._pool = WorkerPool(concurrency=self.concurrency)
        await self._pool.start(self._one_iteration)
        try:
            await asyncio.Future()  # block until cancelled
        except asyncio.CancelledError:
            await self._pool.stop()
            raise

    async def stop(self) -> None:
        if self._pool is not None:
            await self._pool.stop()

    async def tick_once(self) -> None:
        """For tests: run one iteration synchronously."""
        await self._one_iteration()

    async def _one_iteration(self) -> None:
        job = await self.repo.claim_next_job(
            lease_holder=self.lease_holder, lease_seconds=self.lease_seconds,
        )
        if job is None:
            await asyncio.sleep(self.poll_interval_s)
            return

        handler = self.handlers.get(job.phase)
        if handler is None:
            await self._fail_loud(
                job, ClassifiedError(
                    error_class=ErrorClass.PERMANENT, code="UNCLASSIFIED",
                    message=f"no handler for phase {job.phase.value}",
                    hint="server misconfiguration — file a bug",
                ),
            )
            return

        # Build the PhaseContext + injected aws_creds/pulumi_env from
        # ProvidersConfig (cached per-job for now; the load is short-lived).
        aws_creds, pulumi_env = (None, {})
        if self.load_aws_context is not None:
            aws_creds, pulumi_env = await self.load_aws_context(job)
        ctx = PhaseContext(
            repo=self.repo, db=self.db, emit_event=self.emit_event,
            aws_creds=aws_creds, pulumi_env=pulumi_env,
        )

        # Run the handler with lease renewal in parallel. We wrap the
        # handler call in a coroutine that sets `stop` in a finally block
        # so the renew_loop exits whether the handler returns or raises.
        stop = asyncio.Event()
        result: PhaseResult | None = None
        handler_exc: BaseException | None = None

        async def _run_handler() -> PhaseResult:
            try:
                return await handler.run(job, ctx)
            finally:
                stop.set()

        try:
            async with asyncio.TaskGroup() as tg:
                tg.create_task(renew_loop(
                    repo=self.repo, job_id=job.id, lease_holder=self.lease_holder,
                    renew_interval_s=self.renew_interval_s,
                    lease_seconds=self.lease_seconds, stop=stop,
                ))
                runner = tg.create_task(_run_handler())
                # Wait for the runner; on completion or error, stop the renewer.
            result = runner.result()
        except* ProvisioningError as eg:
            # PEP 654: can't `return` from inside except* — capture and handle below.
            stop.set()
            handler_exc = eg.exceptions[0]
        except* Exception as eg:
            stop.set()
            handler_exc = eg.exceptions[0]

        if handler_exc is not None:
            await self._handle_error(job, handler_exc)
            return

        # Successful PhaseResult — advance phase (or stay).
        if result.next_phase is None:
            # Handler asked to retry; treat as transient with no exception.
            await self.repo.release_lease(job_id=job.id, lease_holder=self.lease_holder)
            return
        await self.repo.transition_to(
            job_id=job.id, current_phase=job.phase, next_phase=result.next_phase,
            lease_holder=self.lease_holder, outputs=result.outputs,
        )
        if result.event is not None:
            await self.emit_event(
                pool_id=job.pool_id, node_id=job.node_id,
                phase=result.event.phase, status=result.event.status,
                message=result.event.message, extra=result.event.extra,
            )

    async def _handle_error(self, job: ProvisioningJob, exc: BaseException) -> None:
        try:
            ce = classify_error(exc)
        except (asyncio.CancelledError, KeyboardInterrupt):
            raise

        if ce.error_class == ErrorClass.TRANSIENT:
            new_attempt = job.attempt_count + 1
            if new_attempt >= TRANSIENT_MAX_ATTEMPTS:
                # Escalate to permanent.
                escalated = ClassifiedError(
                    error_class=ErrorClass.PERMANENT,
                    code="RETRIES_EXHAUSTED",
                    message=f"gave up after {TRANSIENT_MAX_ATTEMPTS} transient "
                            f"failures: {ce.message}",
                    hint=ce.hint,
                )
                await self.repo.fail(
                    job_id=job.id, current_phase=job.phase,
                    lease_holder=self.lease_holder, error=escalated,
                )
                await self.emit_event(
                    pool_id=job.pool_id, node_id=job.node_id, phase=job.phase,
                    status="failed", message=escalated.message,
                    extra={"code": escalated.code, "class": "PERMANENT"},
                )
                return
            now = job.updated_at.astimezone(timezone.utc) if job.updated_at else None
            now = now or datetime.now(timezone.utc)
            await self.repo.schedule_retry(
                job_id=job.id, current_phase=job.phase,
                lease_holder=self.lease_holder,
                next_attempt_after=next_attempt_after(new_attempt, now=now),
                attempt_count=new_attempt, error=ce,
            )
            await self.emit_event(
                pool_id=job.pool_id, node_id=job.node_id, phase=job.phase,
                status="log",
                message=f"transient failure ({ce.code}); retrying (attempt {new_attempt})",
                extra={"code": ce.code, "class": ce.error_class.value},
            )
            return

        # PERMANENT / INFRASTRUCTURE → fail terminal.
        await self._fail_loud(job, ce)

    async def _fail_loud(self, job: ProvisioningJob, ce: ClassifiedError) -> None:
        await self.repo.fail(
            job_id=job.id, current_phase=job.phase,
            lease_holder=self.lease_holder, error=ce,
        )
        await self.emit_event(
            pool_id=job.pool_id, node_id=job.node_id, phase=job.phase,
            status="failed", message=ce.message,
            extra={"code": ce.code, "class": ce.error_class.value},
        )
        if ce.hint:
            await self.emit_event(
                pool_id=job.pool_id, node_id=job.node_id, phase=job.phase,
                status="log", message=ce.hint, extra={"hint": True},
            )

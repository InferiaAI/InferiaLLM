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


def _select_runner_exception(
    eg: BaseExceptionGroup, runner: asyncio.Task | None,
) -> BaseException | None:
    """Return the exception that originated from the runner task, or None
    if no runner exception is present (then we treat the renewer's
    exception as the failure, which is also reasonable)."""
    if runner is None:
        return None
    if runner.done() and not runner.cancelled():
        rexc = runner.exception()
        if rexc is not None:
            return rexc
    return None


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
        self.now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)
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

    async def stop_with_drain(self, *, grace_seconds: float = 30.0) -> None:
        """Stop accepting new jobs; wait up to grace_seconds for in-flight
        handlers to complete; then cancel them. Leases stay set with
        their original TTL — the next reconciler boot picks them up."""
        if self._pool is None:
            return
        try:
            await asyncio.wait_for(self._pool.stop(), timeout=grace_seconds)
        except asyncio.TimeoutError:
            logger.warning(
                "shutdown grace expired (%.1fs); cancelling in-flight handlers",
                grace_seconds,
            )
            # WorkerPool.stop already sets the stop event; the await above
            # timed out because handlers are still running. We rely on
            # asyncio task cancellation propagating from process termination.

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
        renewer_only_failure: bool = False
        runner: asyncio.Task | None = None

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
            runner_exc = _select_runner_exception(eg, runner)
            if runner_exc is None:
                # Renewer (or something else) failed but the handler succeeded —
                # release lease and let next claim pick it up. Log warning.
                logger.warning(
                    "Renewer raised; handler outcome lost", exc_info=True,
                )
                renewer_only_failure = True
            else:
                handler_exc = runner_exc
        except* Exception as eg:
            stop.set()
            runner_exc = _select_runner_exception(eg, runner)
            if runner_exc is None:
                logger.warning(
                    "Renewer raised; handler outcome lost", exc_info=True,
                )
                renewer_only_failure = True
            else:
                handler_exc = runner_exc

        if renewer_only_failure:
            await self.repo.release_lease(
                job_id=job.id, lease_holder=self.lease_holder,
            )
            return
        if handler_exc is not None:
            await self._handle_error(job, handler_exc)
            return

        # Successful PhaseResult — advance phase (or stay).
        if result.next_phase is None:
            # Handler asked to stay in phase (e.g. waiting for an external
            # condition without raising). Treat as transient retry — bump
            # attempt_count and schedule a backoff per the documented
            # contract in PhaseResult's docstring.
            new_attempt = job.attempt_count + 1
            ce = ClassifiedError(
                error_class=ErrorClass.TRANSIENT,
                code="HANDLER_RETRY_REQUESTED",
                message=f"{job.phase.value} handler returned next_phase=None",
                hint=None,
            )
            if new_attempt >= TRANSIENT_MAX_ATTEMPTS:
                escalated = ClassifiedError(
                    error_class=ErrorClass.PERMANENT,
                    code="RETRIES_EXHAUSTED",
                    message=f"gave up after {TRANSIENT_MAX_ATTEMPTS} "
                            f"retry-requests",
                    hint=None,
                )
                await self._fail_loud(job, escalated)
                return
            ok = await self.repo.schedule_retry(
                job_id=job.id, current_phase=job.phase,
                lease_holder=self.lease_holder,
                next_attempt_after=next_attempt_after(
                    new_attempt, now=self.now(),
                ),
                attempt_count=new_attempt, error=ce,
            )
            if not ok:
                logger.warning(
                    "schedule_retry rejected (lease/phase guard); "
                    "skipping event emission",
                )
                return
            if result.event is not None:
                await self.emit_event(
                    pool_id=job.pool_id, node_id=job.node_id,
                    phase=result.event.phase, status=result.event.status,
                    message=result.event.message, extra=result.event.extra,
                )
            return
        ok = await self.repo.transition_to(
            job_id=job.id, current_phase=job.phase, next_phase=result.next_phase,
            lease_holder=self.lease_holder, outputs=result.outputs,
        )
        if not ok:
            logger.warning(
                "transition_to rejected (lease/phase guard); "
                "skipping event emission",
            )
            return
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
                # Escalate to permanent — delegate to _fail_loud so the
                # hint event is emitted consistently with other terminal
                # failure paths.
                escalated = ClassifiedError(
                    error_class=ErrorClass.PERMANENT,
                    code="RETRIES_EXHAUSTED",
                    message=f"gave up after {TRANSIENT_MAX_ATTEMPTS} transient "
                            f"failures: {ce.message}",
                    hint=ce.hint,
                )
                await self._fail_loud(job, escalated)
                return
            now = job.updated_at.astimezone(timezone.utc) if job.updated_at else None
            now = now or self.now()
            ok = await self.repo.schedule_retry(
                job_id=job.id, current_phase=job.phase,
                lease_holder=self.lease_holder,
                next_attempt_after=next_attempt_after(new_attempt, now=now),
                attempt_count=new_attempt, error=ce,
            )
            if not ok:
                logger.warning(
                    "schedule_retry rejected (lease/phase guard); "
                    "skipping event emission",
                )
                return
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
        ok = await self.repo.fail(
            job_id=job.id, current_phase=job.phase,
            lease_holder=self.lease_holder, error=ce,
        )
        if not ok:
            logger.warning(
                "fail rejected (lease/phase guard); skipping event emission",
            )
            return
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

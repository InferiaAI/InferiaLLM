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

from orchestration.provisioning_state_machine.errors import (
    ProvisioningError,
)
from orchestration.provisioning_state_machine.jobs.model import (
    ClassifiedError, ErrorClass, Phase, PhaseResult, ProvisioningJob,
)
from orchestration.provisioning_state_machine.phases.base import (
    PhaseContext, PhaseHandler,
)
from orchestration.provisioning_state_machine.reconciler.concurrency import (
    WorkerPool,
)
from orchestration.provisioning_state_machine.reconciler.lease import (
    renew_loop,
)
from orchestration.provisioning_state_machine.retry.backoff import (
    TRANSIENT_MAX_ATTEMPTS, next_attempt_after,
)
from orchestration.provisioning_state_machine.retry.classifier import (
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
        inventory_repo: Any = None,
        worker_registry: Any = None,
        pool_repo: Any = None,
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
        # worker_registry is the in-memory node_id → live-WS cache. On a
        # successful node teardown the reconciler calls
        # worker_registry.detach_node(node_id) to close the worker socket +
        # any open shell/logs streams (defense-in-depth: the worker WS read
        # loop already detaches on disconnect). Optional — tests / split
        # deployments that don't run the worker controller pass None.
        self.worker_registry = worker_registry
        # inventory_repo bridges the provisioning_jobs state machine to
        # compute_inventory.state. On every terminal transition (ready /
        # terminated) and on _fail_loud (failed) the reconciler calls
        # inventory_repo.set_state(node_id, terminal_state) so the
        # dashboard's "is this node alive" view (which reads from
        # compute_inventory.state) reflects the state machine outcome.
        # Optional: tests pass None to skip the bridge.
        self.inventory_repo = inventory_repo
        # pool_repo (ComputePoolRepository) drives PHASE 2 of pool teardown.
        # A pool delete only SOFT-deletes the pool (is_active=FALSE /
        # lifecycle_state='terminating') and fires per-node teardown via
        # force_cancel_pool; the EC2 destroys are async (per-node
        # CancelHandler → pulumi destroy). When the LAST node of a
        # 'terminating' pool is purged here, _teardown_node hard-deletes the
        # pool + its pool-scoped residue via pool_repo.finalize_pool_delete
        # and runs the boto3 pool sweep as the orphan-EC2 backstop. Optional:
        # tests / split deployments that don't run the pool finalizer pass
        # None (the per-node teardown still runs, the pool row just stays
        # soft-deleted).
        self.pool_repo = pool_repo
        self.now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)
        self._pool: WorkerPool | None = None

    @staticmethod
    def _region_for_job(job: ProvisioningJob) -> str | None:
        """Resolve the AWS region for the orphan sweep.

        Preferred source is the job spec (``region`` is a required field the
        PreflightHandler validates and the add-node route writes). Fall back
        to ``pulumi_stack_outputs['region']`` (PreflightHandler echoes it
        there) for older rows whose spec predates the field. Returns None if
        neither carries a region — the sweep then short-circuits to [].
        """
        spec = job.spec or {}
        region = spec.get("region")
        if region:
            return str(region)
        outputs = job.pulumi_stack_outputs or {}
        region = outputs.get("region")
        return str(region) if region else None

    async def _teardown_node(self, job: ProvisioningJob) -> None:
        """Canonical, leak-proof teardown after a successful pulumi destroy.

        Runs ONLY on the CANCELLING → TERMINATED transition (destroy
        confirmed — including the idempotent 'missing stack' case, which the
        CancelHandler returns as success). Replaces the old soft
        ``inventory.set_state(node_id, 'terminated')`` write, which left the
        compute_inventory row + provisioning_jobs / events / tokens piling up
        forever and never reclaimed orphan EC2.

        Order:
          1. ``sweep_node_instances`` FIRST — boto3 tag backstop that
             terminates any orphan/duplicate EC2 tagged InferiaNodeId that
             pulumi never tracked (retry double-launches). Best-effort.
          2. ``inventory.purge_node`` — ONE-tx hard purge of the node row and
             all its DB residue (replaces the soft terminated write).
          3. ``worker_registry.detach_node`` — close the in-memory worker
             socket + shell/logs streams (defense-in-depth; the worker WS
             read loop also detaches on disconnect).

        Every step is best-effort and isolated: a failure in one is logged
        and never blocks the others, so a flaky AWS describe can't strand the
        DB purge and vice-versa.
        """
        node_id = job.node_id

        # 1. Orphan/duplicate EC2 sweep (boto3 tag backstop).
        region = self._region_for_job(job)
        if region:
            try:
                from orchestration.adapter_engine.aws_orphan_sweep import (
                    resolve_sweep_aws_env,
                    sweep_node_instances,
                )
                # Resolve creds HERE — on the reconciler's main loop where the
                # asyncpg-backed ProvidersConfig session works. Resolving them
                # inside the to_thread worker (a thread with no running loop)
                # would spin up a new loop and blow up cross-loop, silently
                # no-op'ing the sweep in production. Best-effort: None ⇒ the
                # sweep logs a no-creds WARNING and returns [].
                aws_env = await resolve_sweep_aws_env()
                terminated = await asyncio.to_thread(
                    sweep_node_instances, str(node_id), region, aws_env,
                )
                if terminated:
                    logger.info(
                        "orphan sweep terminated %d EC2 for node=%s: %s",
                        len(terminated), node_id, ", ".join(terminated),
                    )
            except Exception:
                logger.exception(
                    "orphan sweep failed for node=%s region=%s; continuing "
                    "with DB purge", node_id, region,
                )
        else:
            logger.warning(
                "no region for node=%s; skipping orphan EC2 sweep "
                "(pulumi destroy is still authoritative)", node_id,
            )

        # 2. Hard DB purge (replaces the soft state='terminated' write).
        if self.inventory_repo is not None:
            try:
                await self.inventory_repo.purge_node(node_id)
            except Exception:
                logger.exception(
                    "purge_node(%s) failed; the inventory row + residue may "
                    "linger (EC2 already swept/destroyed)", node_id,
                )

        # 2b. PHASE-2 pool finalizer. If this node's purge was the LAST one of
        # a pool whose delete request put it in lifecycle_state='terminating',
        # hard-delete the pool + its pool-scoped residue and sweep any orphan
        # pool EC2. Best-effort + isolated: a finalizer failure here never
        # blocks the worker-registry detach below, and a still-populated pool
        # (more nodes to tear down) is simply left for the next node's
        # teardown to finalize.
        await self._finalize_pool_if_empty(job)

        # 3. Drop the in-memory worker connection (defense-in-depth).
        if self.worker_registry is not None:
            try:
                await self.worker_registry.detach_node(str(node_id))
            except Exception:
                logger.exception(
                    "worker_registry.detach_node(%s) failed; the worker WS "
                    "close path will still detach on disconnect", node_id,
                )

    async def _finalize_pool_if_empty(self, job: ProvisioningJob) -> None:
        """PHASE 2 of pool teardown: hard-delete a pool once its last node is
        gone.

        Trigger condition (the clean seam — runs right after a node's
        ``purge_node`` in :meth:`_teardown_node`):

          * the pool is in ``lifecycle_state = 'terminating'`` (set by the
            delete request — NOT 'terminated' yet, because the EC2 destroys
            were async), AND
          * the pool now has ZERO ``compute_inventory`` rows (this node's purge
            was the last one).

        When both hold we, in order:

          1. ``pool_repo.finalize_pool_delete(pool_id)`` — ONE transaction that
             deletes ``node_provisioning_events`` (no FK) +
             ``worker_bootstrap_tokens`` (pool tokens) + the ``compute_pools``
             row (HARD delete → the ON DELETE CASCADE FKs fire for
             ``autoscaler_state`` and any stragglers; frees the unique
             pool_name).
          2. ``sweep_pool_instances(pool_id, region)`` — OUTSIDE the tx,
             best-effort boto3 backstop that terminates any pool EC2 a per-node
             ``force_cancel_pool`` skipped (a node whose job already reached the
             terminal 'terminated' phase, or a retry double-launch Pulumi never
             tracked). This is the re-arm backstop: rather than re-enqueueing
             already-'terminated' jobs (which risks an unbounded loop), we rely
             on the tag sweep to reclaim those leaks.

        Best-effort and fully isolated: a None ``pool_repo`` (tests / split
        deploys) short-circuits; every step is wrapped so a failure is logged
        and never propagates back into the node teardown flow. Idempotent —
        ``finalize_pool_delete`` returns False (and we skip the sweep's "pool
        finalized" log) if the pool row was already removed by a concurrent
        teardown.
        """
        if self.pool_repo is None:
            return
        pool_id = job.pool_id
        try:
            lifecycle = await self.pool_repo.get_lifecycle_state(pool_id)
        except Exception:
            logger.exception(
                "pool finalizer: get_lifecycle_state(%s) failed; leaving the "
                "pool row soft-deleted", pool_id,
            )
            return
        if lifecycle != "terminating":
            # Not a pool being deleted (e.g. a single node dropped from a live
            # pool) — nothing to finalize.
            return
        try:
            remaining = await self.pool_repo.count_live_inventory(pool_id)
        except Exception:
            logger.exception(
                "pool finalizer: count_live_inventory(%s) failed; deferring "
                "finalize to the next node teardown", pool_id,
            )
            return
        if remaining > 0:
            # More nodes still tearing down; the LAST one to be purged will
            # finalize the pool.
            logger.info(
                "pool finalizer: pool=%s still has %d inventory row(s); "
                "deferring hard-delete", pool_id, remaining,
            )
            return

        # Last node gone → hard-delete the pool + pool-scoped residue.
        try:
            deleted = await self.pool_repo.finalize_pool_delete(pool_id)
        except Exception:
            logger.exception(
                "pool finalizer: finalize_pool_delete(%s) failed; the pool "
                "row + pool-scoped residue may linger", pool_id,
            )
            return
        if deleted:
            logger.info(
                "pool finalizer: hard-deleted pool=%s and its pool-scoped "
                "residue (last node purged)", pool_id,
            )

        # Orphan/duplicate pool-EC2 sweep (boto3 tag backstop) — OUTSIDE the
        # DB tx. Catches EC2 the per-node force_cancel skipped (terminated-job
        # nodes / retry double-launches). Best-effort.
        region = self._region_for_job(job)
        if region:
            try:
                from orchestration.adapter_engine.aws_orphan_sweep import (
                    resolve_sweep_aws_env,
                    sweep_pool_instances,
                )
                # Resolve creds on the main loop (asyncpg-bound session works
                # here) BEFORE the to_thread sweep — see _teardown_node. None ⇒
                # the sweep logs a no-creds WARNING and returns [].
                aws_env = await resolve_sweep_aws_env()
                terminated = await asyncio.to_thread(
                    sweep_pool_instances, str(pool_id), region, aws_env,
                )
                if terminated:
                    logger.info(
                        "pool sweep terminated %d orphan EC2 for pool=%s: %s",
                        len(terminated), pool_id, ", ".join(terminated),
                    )
            except Exception:
                logger.exception(
                    "pool sweep failed for pool=%s region=%s; per-node pulumi "
                    "destroy is still authoritative", pool_id, region,
                )
        else:
            logger.warning(
                "pool finalizer: no region for pool=%s; skipping orphan EC2 "
                "sweep (per-node pulumi destroy is still authoritative)",
                pool_id,
            )

    async def _inventory_set_state(
        self, *, node_id: Any, state: str,
    ) -> None:
        """Mirror a terminal phase onto compute_inventory.state.

        Best-effort: logs and swallows on failure so a stuck inventory
        UPDATE never blocks the state machine from finishing. The
        provisioning_jobs row is the authoritative source of truth;
        compute_inventory is the user-facing mirror.
        """
        if self.inventory_repo is None:
            return
        try:
            await self.inventory_repo.set_state(node_id=node_id, state=state)
        except Exception:
            logger.exception(
                "inventory_repo.set_state(%s, %s) failed; "
                "compute_inventory may diverge from provisioning_jobs",
                node_id, state,
            )

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
        # Cold EC2 boot + a large GPU worker-image docker pull + nvidia
        # toolkit install routinely exceeds the 600s default. Allow an env
        # override so operators can raise it for GPU images without a
        # rebuild. Falls back to PhaseContext's 600s default.
        import os as _os
        try:
            _bootstrap_timeout = float(
                _os.getenv("INFERIA_BOOTSTRAP_TIMEOUT_S", "") or 900.0
            )
        except (TypeError, ValueError):
            _bootstrap_timeout = 900.0
        ctx = PhaseContext(
            repo=self.repo, db=self.db, emit_event=self.emit_event,
            aws_creds=aws_creds, pulumi_env=pulumi_env,
            bootstrap_timeout_s=_bootstrap_timeout,
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
        # Mirror terminal phases onto compute_inventory.state so the
        # dashboard's "is this node alive" check (which reads inventory,
        # not provisioning_jobs) reflects the state machine outcome.
        # Phase.FAILED is handled separately in _fail_loud.
        #
        # TERMINATED is special: the destroy has SUCCEEDED (CancelHandler
        # returns next_phase=TERMINATED only on a confirmed/idempotent
        # destroy — a real failure raises and never reaches here). Instead of
        # a soft state='terminated' write that leaks the row + EC2 orphans,
        # we run the canonical leak-proof teardown (sweep orphan EC2, hard
        # purge the DB residue, detach the worker conn) AFTER emitting the
        # terminal event so the purge sweeps that event row too — zero
        # residue. We use only in-memory ``job`` fields below, so the row
        # vanishing under us is safe.
        if result.next_phase == Phase.READY:
            await self._inventory_set_state(node_id=job.node_id, state="ready")
        if result.event is not None:
            await self.emit_event(
                pool_id=job.pool_id, node_id=job.node_id,
                phase=result.event.phase, status=result.event.status,
                message=result.event.message, extra=result.event.extra,
            )
        if result.next_phase == Phase.TERMINATED:
            await self._teardown_node(job)

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

    async def _fail_dependent_deployments(
        self, *, node_id: Any, ce: ClassifiedError,
    ) -> None:
        """Mark deployments bound to a permanently-failed node as FAILED.

        Each pool-first deploy binds to its own placeholder node
        (``model_deployments.target_node_id``). When that node's provisioning
        fails terminally, the deploy would otherwise stay PENDING_NODE forever.
        Best-effort: logs and swallows so it never blocks the state machine.
        The message carries the actionable reason (hint preferred) so the
        dashboard shows e.g. "node provisioning failed (QUOTA_EXCEEDED): ...".
        """
        reason = ce.hint or ce.message or ce.code
        message = f"node provisioning failed ({ce.code}): {reason}"[:500]
        try:
            await self.repo.fail_deployments_for_node(
                node_id=node_id, message=message,
            )
        except Exception:
            logger.exception(
                "fail_deployments_for_node(%s) failed; a deployment may hang "
                "in PENDING_NODE", node_id,
            )

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
        # Mirror the terminal failure onto compute_inventory.state so the
        # dashboard's failed banner renders without waiting for a manual
        # poll/refresh of the provisioning_jobs row.
        await self._inventory_set_state(node_id=job.node_id, state="failed")
        # Fail the deployment(s) bound to this dead node, with the real reason,
        # so they don't hang in PENDING_NODE forever (the node will never come).
        await self._fail_dependent_deployments(node_id=job.node_id, ce=ce)
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

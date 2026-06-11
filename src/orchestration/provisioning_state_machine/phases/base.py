"""PhaseHandler interface + PhaseContext dependency carrier.

A handler is anything with:
  - name: Phase   class-level attribute saying which phase it handles
  - async def run(job: ProvisioningJob, ctx: PhaseContext) -> PhaseResult

Handlers either return PhaseResult (success or "stay in phase to retry")
or raise. The reconciler classifies any exception via classify_error
and writes the outcome to the jobs table.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Protocol, runtime_checkable

from orchestration.provisioning_state_machine.jobs.model import (
    Phase, PhaseResult, ProvisioningJob,
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def stack_name_for_job(job) -> str:
    """Deterministic Pulumi stack name for a provisioning job.

    MUST be <= 100 chars (Pulumi rejects longer with
    'a stack name cannot exceed 100 characters') and MUST be identical
    between PulumiUpHandler (up) and CancelHandler (destroy) so the stack
    can be reopened to tear down. node_id alone uniquely identifies the
    provision; the old f"{org_id}-{pool_id}-{node_id}" was three UUIDs
    (~110 chars) and failed pulumi up at stack creation.
    """
    return f"inferia-{job.node_id}"


@dataclass
class PhaseContext:
    """Carries dependencies handlers need, injected by the reconciler.

    `emit_event` is the bound events.emit_event helper from this DB.
    `now` is a callable so tests can inject a fake clock.
    """
    repo: Any                          # ProvisioningJobRepository
    db: Any                            # database pool with .acquire()
    emit_event: Callable[..., Awaitable[None]]
    now: Callable[[], datetime] = field(default=_utc_now)
    bootstrap_timeout_s: float = 600.0

    # AWS-specific extras populated by PreflightHandler for downstream
    # handlers. Kept loose (Any) so tests don't need full plumbing.
    aws_creds: Any = None
    pulumi_env: dict[str, str] = field(default_factory=dict)


@runtime_checkable
class PhaseHandler(Protocol):
    """Phase handler interface. Implementations are stateless: all
    state lives in `job` and is mutated only through `ctx.repo`."""

    name: Phase

    async def run(
        self, job: ProvisioningJob, ctx: PhaseContext,
    ) -> PhaseResult: ...

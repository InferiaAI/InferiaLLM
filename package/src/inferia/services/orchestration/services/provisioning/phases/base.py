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

from inferia.services.orchestration.services.provisioning.jobs.model import (
    Phase, PhaseResult, ProvisioningJob,
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


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

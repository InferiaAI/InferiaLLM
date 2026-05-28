"""ProvisioningJob domain model + Phase/ErrorClass enums + supporting
dataclasses (ClassifiedError, EventLine, PhaseResult).

The Pydantic ProvisioningJob is used by repository read paths and the
HTTP layer; the dataclasses are used inside handler/reconciler code
where immutability + value semantics are more natural than Pydantic."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class Phase(str, Enum):
    PENDING       = "pending"
    PREFLIGHT     = "preflight"
    PROVISIONING  = "provisioning"
    BOOTSTRAPPING = "bootstrapping"
    READY         = "ready"
    FAILED        = "failed"
    CANCELLING    = "cancelling"
    TERMINATED    = "terminated"

    @property
    def is_terminal(self) -> bool:
        return self in _TERMINAL_PHASES


_TERMINAL_PHASES: frozenset[Phase] = frozenset(
    {Phase.READY, Phase.FAILED, Phase.TERMINATED}
)


NON_TERMINAL_NON_CANCELLING: frozenset[Phase] = frozenset({
    Phase.PENDING, Phase.PREFLIGHT, Phase.PROVISIONING, Phase.BOOTSTRAPPING,
})
"""The set of phases the claim query considers when looking for the next
job to run (excluding 'cancelling' which is handled specially)."""


CLAIMABLE_PHASES: frozenset[Phase] = NON_TERMINAL_NON_CANCELLING | {Phase.CANCELLING}


class ErrorClass(str, Enum):
    TRANSIENT      = "TRANSIENT"
    PERMANENT      = "PERMANENT"
    INFRASTRUCTURE = "INFRASTRUCTURE"


@dataclass(frozen=True)
class ClassifiedError:
    """Output of `classify_error(exc)`. The reconciler uses this to decide
    retry vs fail and to populate the job row's error_* columns."""
    error_class: ErrorClass
    code: str
    message: str
    hint: str | None = None


EventStatus = Literal["running", "succeeded", "failed", "log"]


@dataclass(frozen=True)
class EventLine:
    """One row to emit into node_provisioning_events."""
    phase: Phase
    status: EventStatus
    message: str
    extra: dict[str, Any] | None = None


@dataclass(frozen=True)
class PhaseResult:
    """Successful handler return value.

    - next_phase=None means "stay in current phase" (transient retry path);
      the reconciler will increment attempt_count and schedule a backoff.
    - outputs: dict merged into provisioning_jobs.pulumi_stack_outputs.
    - event: single summary EventLine to emit on success (handlers may
      emit additional `log`-status events while running; this is the
      terminal one for the phase transition).
    """
    next_phase: Phase | None
    outputs: dict[str, Any] | None = None
    event: EventLine | None = None


class ProvisioningJob(BaseModel):
    """Pydantic mirror of a provisioning_jobs row.

    Use `ProvisioningJob.from_row(record)` to build from asyncpg.Record
    or a dict; that translates phase/error_class strings to enum values
    and handles JSONB columns."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    node_id: UUID
    pool_id: UUID
    org_id: str
    provider: str
    spec: dict[str, Any] = Field(default_factory=dict)

    phase: Phase
    attempt_count: int
    next_attempt_after: datetime | None = None

    last_error_code: str | None = None
    last_error_message: str | None = None
    last_error_hint: str | None = None
    error_class: ErrorClass | None = None

    lease_holder: str | None = None
    lease_expires_at: datetime | None = None

    pulumi_stack_outputs: dict[str, Any] | None = None

    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_row(cls, row: Any) -> "ProvisioningJob":
        """Build from an asyncpg.Record or dict-like mapping."""
        return cls(
            id=row["id"],
            node_id=row["node_id"],
            pool_id=row["pool_id"],
            org_id=row["org_id"],
            provider=row["provider"],
            spec=row["spec"] or {},
            phase=Phase(row["phase"]),
            attempt_count=row["attempt_count"],
            next_attempt_after=row["next_attempt_after"],
            last_error_code=row["last_error_code"],
            last_error_message=row["last_error_message"],
            last_error_hint=row["last_error_hint"],
            error_class=ErrorClass(row["error_class"]) if row["error_class"] else None,
            lease_holder=row["lease_holder"],
            lease_expires_at=row["lease_expires_at"],
            pulumi_stack_outputs=row["pulumi_stack_outputs"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

"""Extension-point protocols and the value objects they exchange.

Contract: docs/platform/interfaces.md §6 (protocols), §8 (usage event).

Everything here is standard-library only so a plugin can implement a protocol
without importing any InferiaLLM service package. Protocols are
``runtime_checkable`` so the registry can reject an implementation that is
missing a method; note that ``isinstance`` against a runtime-checkable
Protocol only checks that the attributes exist, not their signatures
(https://docs.python.org/3/library/typing.html#typing.runtime_checkable), so
the registry additionally checks that protocol methods are coroutines.

``db`` keyword arguments: the open-source defaults reuse the caller's
request-scoped SQLAlchemy session where the pre-extension code did. Plugins
may ignore it. It is typed ``Any`` so this module does not depend on
SQLAlchemy.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Protocol, Sequence, runtime_checkable


def _uuid() -> str:
    return str(uuid.uuid4())


def _utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _utcnow_naive() -> datetime:
    # audit_logs.timestamp is a naive UTC column (see AuditService).
    return datetime.now(timezone.utc).replace(tzinfo=None)


# --------------------------------------------------------------------- audit


@dataclass(frozen=True)
class AuditEvent:
    """One audit-log record. Mirrors the ``audit_logs`` columns."""

    action: str
    user_id: Optional[str] = None
    org_id: Optional[str] = None
    category: Optional[str] = None
    resource_type: Optional[str] = None
    resource_id: Optional[str] = None
    details: Optional[Dict[str, Any]] = None
    ip_address: Optional[str] = None
    status: str = "success"
    id: str = field(default_factory=_uuid)
    timestamp: datetime = field(default_factory=_utcnow_naive)


@runtime_checkable
class AuditSink(Protocol):
    async def emit(self, event: AuditEvent) -> None:
        """Persist or forward one audit event. Must not raise for transient
        backend errors on the request path; log them instead."""
        ...


# --------------------------------------------------------------------- usage


@dataclass(frozen=True)
class UsageEvent:
    """interfaces.md §8 usage event. No prompts, completions or client IPs.

    ``total_tokens`` is carried in addition to §8's prompt/completion counts
    because the existing daily usage rows store the caller-reported total.
    """

    user_id: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    org_id: Optional[str] = None
    api_key_id: Optional[str] = None
    deployment_id: Optional[str] = None
    request_id: Optional[str] = None
    request_type: Optional[str] = None
    workload_class: Optional[str] = None
    status_code: Optional[int] = None
    cancelled: bool = False
    shed: bool = False
    input_count: Optional[int] = None
    n_items: Optional[int] = None
    ttft_ms: Optional[int] = None
    latency_ms: Optional[int] = None
    is_streaming: Optional[bool] = None
    schema_version: str = "1"
    event_id: str = field(default_factory=_uuid)
    ts: str = field(default_factory=_utc_iso)


@runtime_checkable
class UsageSink(Protocol):
    async def record(self, events: Sequence[UsageEvent], *, db: Any = None) -> None:
        """Record a batch of usage events. Delivery is at-least-once;
        implementations dedupe on ``event_id`` where they can."""
        ...


# --------------------------------------------------------------------- quota


@dataclass(frozen=True)
class QuotaContext:
    """Who is asking, for which model. ``user_id`` may be a user, org or API-key id."""

    user_id: str
    model: str = "default"
    org_id: Optional[str] = None


@dataclass(frozen=True)
class UsageEstimate:
    """Expected cost of the request being admitted (0 = unknown)."""

    prompt_tokens: int = 0
    max_output_tokens: int = 0
    total_tokens: int = 0
    gpu_seconds: float = 0.0


@dataclass(frozen=True)
class QuotaDecision:
    allowed: bool
    reason: Optional[str] = None
    retry_after_s: Optional[float] = None

    @classmethod
    def allow(cls) -> "QuotaDecision":
        return cls(allowed=True)

    @classmethod
    def deny(cls, reason: str, retry_after_s: Optional[float] = None) -> "QuotaDecision":
        return cls(allowed=False, reason=reason, retry_after_s=retry_after_s)


@runtime_checkable
class QuotaPolicy(Protocol):
    async def check(
        self, ctx: QuotaContext, estimate: UsageEstimate, *, db: Any = None
    ) -> QuotaDecision:
        """Decide whether the request fits the caller's budget."""
        ...


# --------------------------------------------------------------------- auth


@runtime_checkable
class TokenVerifier(Protocol):
    async def verify(self, token: str, *, db: Any = None) -> Any:
        """Resolve a bearer token to the gateway's ``UserContext``
        (interfaces.md §7). Raise ``HTTPException(401)`` on failure."""
        ...

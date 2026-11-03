"""Open-source ``quota_policy`` and ``usage_sink`` defaults (interfaces.md §6).

Both delegate to the existing policy engine, looked up at call time so the
engine singleton stays the single source of truth.
"""

from typing import Any, Sequence

from fastapi import HTTPException, status

from common.extensions.protocols import QuotaContext, QuotaDecision, UsageEstimate, UsageEvent


def _engine():
    from api_gateway.policy import engine

    return engine.policy_engine


class PolicyEngineQuotaPolicy:
    """Daily request/token limits per org (``PolicyEngine.check_quota``).

    The engine signals "over quota" with HTTP 429; that becomes a deny
    decision carrying the same detail text. Any other error propagates
    unchanged. ``estimate`` is unused: the daily limits are checked against
    usage already recorded, as before.
    """

    async def check(
        self, ctx: QuotaContext, estimate: UsageEstimate, *, db: Any = None
    ) -> QuotaDecision:
        try:
            await _engine().check_quota(db, ctx.user_id, ctx.model)
        except HTTPException as e:
            if e.status_code == status.HTTP_429_TOO_MANY_REQUESTS:
                return QuotaDecision.deny(str(e.detail))
            raise
        return QuotaDecision.allow()


class PolicyEngineUsageSink:
    """Upserts the daily ``usage_stats`` row per event (``PolicyEngine.persist_usage_db``)."""

    async def record(self, events: Sequence[UsageEvent], *, db: Any = None) -> None:
        engine = _engine()
        for event in events:
            await engine.persist_usage_event_db(
                db,
                event.event_id,
                event.user_id,
                event.model,
                {
                    "prompt_tokens": event.prompt_tokens,
                    "completion_tokens": event.completion_tokens,
                    "total_tokens": event.total_tokens,
                },
            )

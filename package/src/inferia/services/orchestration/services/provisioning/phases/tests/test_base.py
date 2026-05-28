"""Tests for PhaseHandler protocol + PhaseContext dataclass."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import runtime_checkable

import pytest

from inferia.services.orchestration.services.provisioning.jobs.model import (
    Phase, PhaseResult,
)
from inferia.services.orchestration.services.provisioning.phases.base import (
    PhaseContext, PhaseHandler,
)


def test_phase_context_carries_all_required_fields():
    ctx = PhaseContext(
        repo=object(),
        db=object(),
        emit_event=lambda **kw: None,
        bootstrap_timeout_s=600.0,
    )
    assert ctx.bootstrap_timeout_s == 600.0
    assert callable(ctx.emit_event)


def test_phase_context_now_defaults_to_utc_now():
    ctx = PhaseContext(
        repo=object(), db=object(),
        emit_event=lambda **kw: None,
    )
    now = ctx.now()
    assert now.tzinfo is not None


def test_phase_handler_protocol_signature():
    """Anything with a `name: Phase` attribute and an async `run(job, ctx)`
    method satisfies PhaseHandler."""
    class _MyHandler:
        name = Phase.PREFLIGHT
        async def run(self, job, ctx):
            return PhaseResult(next_phase=Phase.PROVISIONING)

    h: PhaseHandler = _MyHandler()  # type: ignore[assignment]
    assert h.name == Phase.PREFLIGHT

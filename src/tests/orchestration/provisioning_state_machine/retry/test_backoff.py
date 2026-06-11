"""Tests for the exponential-backoff-with-jitter helper."""
from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

import pytest

from orchestration.state_machine.retry.backoff import (
    TRANSIENT_MAX_ATTEMPTS,
    next_attempt_after,
)


@pytest.fixture
def fixed_seed():
    random.seed(0)
    yield
    random.seed()


def _delta_seconds(delta_time: datetime, now: datetime) -> float:
    return (delta_time - now).total_seconds()


def test_attempt_1_delay_between_half_and_one_half_base(fixed_seed):
    """attempt=1 → base=2s; delay is base/2 + jitter ∈ [0, base] → [1, 3)."""
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    when = next_attempt_after(1, now=now)
    d = _delta_seconds(when, now)
    assert 1.0 <= d < 3.0


def test_attempt_2_in_window():
    """attempt=2 → base=4; delay ∈ [2, 6)."""
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for _ in range(50):
        d = _delta_seconds(next_attempt_after(2, now=now), now)
        assert 2.0 <= d < 6.0


def test_attempt_5_in_window():
    """attempt=5 → base=32; delay ∈ [16, 48)."""
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for _ in range(50):
        d = _delta_seconds(next_attempt_after(5, now=now), now)
        assert 16.0 <= d < 48.0


def test_attempt_10_capped_at_60s_base():
    """High attempt numbers cap base at 60; delay ∈ [30, 90)."""
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for _ in range(50):
        d = _delta_seconds(next_attempt_after(10, now=now), now)
        assert 30.0 <= d < 90.0


def test_jitter_is_non_zero_statistically():
    """Across many samples, the delays vary (not all the same value)."""
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    samples = {_delta_seconds(next_attempt_after(3, now=now), now) for _ in range(100)}
    assert len(samples) > 50


def test_returns_timezone_aware_datetime():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    when = next_attempt_after(1, now=now)
    assert when.tzinfo is not None


def test_max_attempts_constant_is_five():
    """Spec value: 5 transient attempts before escalating to RETRIES_EXHAUSTED."""
    assert TRANSIENT_MAX_ATTEMPTS == 5

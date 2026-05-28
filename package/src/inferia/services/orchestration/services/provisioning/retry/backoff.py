"""Exponential backoff with jitter for transient retries.

Spec: docs/specs/2026-05-27-aws-ec2-node-allocation-design.md → 'Backoff math'.

Formula:
    base = min(60, 2 ** attempt)
    delay = base/2 + random.uniform(0, base)

So attempt N's delay is in [base/2, 1.5*base). The cap at 60s keeps the
total wait window over 5 attempts bounded by ≈ 2 minutes (the cap kicks
in at attempt ≥ 6 but TRANSIENT_MAX_ATTEMPTS=5 stops us before then).
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta


TRANSIENT_MAX_ATTEMPTS = 5
"""After this many transient failures, the reconciler escalates to a
PERMANENT error with code='RETRIES_EXHAUSTED'."""


def next_attempt_after(attempt: int, *, now: datetime) -> datetime:
    """Return the wall-clock time the reconciler should try the phase again.

    `attempt` is 1-indexed (the attempt about to be retried — so after the
    1st failure pass 1; after the 5th pass 5).

    The returned datetime is in the same timezone as `now`. Callers in
    practice pass `datetime.now(timezone.utc)`.
    """
    base = min(60, 2 ** attempt)
    delay = base / 2 + random.uniform(0, base)
    return now + timedelta(seconds=delay)

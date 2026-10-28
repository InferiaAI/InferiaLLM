"""Credentials an engine container presents to the control plane's HF mirror.

The mirror fetches from HuggingFace with the install's own token, so it must
not serve whoever asks. Orchestration hands each engine a token for its pool;
the gateway checks it. Both hold the internal API key, so the token needs no
storage: it is the pool id plus an HMAC of it under that key.
"""

from __future__ import annotations

import hashlib
import hmac
from typing import Optional

_PURPOSE = b"hf-mirror:"


def _sign(pool_id: str, key: str) -> str:
    return hmac.new(key.encode(), _PURPOSE + pool_id.encode(), hashlib.sha256).hexdigest()


def make(pool_id: str, key: str) -> str:
    """The mirror token for *pool_id*."""
    return f"{pool_id}:{_sign(pool_id, key)}"


def verify(token: Optional[str], key: str) -> Optional[str]:
    """The pool id *token* was issued for, or None if it is not genuine."""
    if not token or not key or ":" not in token:
        return None
    pool_id, _, sig = token.rpartition(":")
    if not pool_id or not hmac.compare_digest(sig, _sign(pool_id, key)):
        return None
    return pool_id

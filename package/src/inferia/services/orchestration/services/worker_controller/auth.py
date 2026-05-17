"""
JWT minting + verification for the worker control plane.

Two token kinds are issued, both signed with the shared ``JWT_SECRET_KEY``:

* **Bootstrap token** — scope=``worker:bootstrap``, default TTL 24h. Operator
  pastes this into a fresh worker's compose env. Worker uses it once to call
  ``/v1/workers/register``.

* **Worker token** — kind=``worker``, default TTL 30d. Returned by ``/register``
  and presented on every subsequent request (WS upgrade, etc.). Distinguished
  from user tokens by the ``kind`` claim so the existing user-auth middleware
  never accepts it, and vice versa.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from jose import JWTError, jwt


# Algorithms we accept. "none" and other unsigned algorithms are forbidden.
_SUPPORTED_ALGORITHMS = frozenset({"HS256", "HS384", "HS512"})

# Reject obviously-weak shared secrets at construction time. Mirrors
# api_gateway's 32-char minimum.
_MIN_SECRET_LEN = 32

# Default TTLs.
_DEFAULT_BOOTSTRAP_TTL = 24 * 3600  # 24h
_DEFAULT_WORKER_TTL = 30 * 24 * 3600  # 30d


class InvalidTokenError(Exception):
    """Raised when a token is missing required claims, has expired, or fails
    signature verification."""


@dataclass(frozen=True)
class BootstrapClaims:
    pool_id: str
    scope: str  # always "worker:bootstrap"
    exp: int    # unix seconds


@dataclass(frozen=True)
class WorkerClaims:
    sub: str        # node id
    pool_id: str
    kind: str       # always "worker"
    exp: int        # unix seconds


class WorkerAuth:
    """Issues + verifies bootstrap and worker tokens."""

    def __init__(self, secret_key: str, algorithm: str = "HS256"):
        if len(secret_key) < _MIN_SECRET_LEN:
            raise ValueError(
                f"WorkerAuth secret must be ≥ {_MIN_SECRET_LEN} chars"
            )
        if algorithm not in _SUPPORTED_ALGORITHMS:
            raise ValueError(
                f"WorkerAuth algorithm {algorithm!r} not in "
                f"{sorted(_SUPPORTED_ALGORITHMS)}"
            )
        self.secret_key = secret_key
        self.algorithm = algorithm

    # --- mint ---------------------------------------------------------------

    def mint_bootstrap_token(
        self,
        *,
        pool_id: str,
        ttl_seconds: int | None = None,
    ) -> str:
        ttl = min(ttl_seconds or _DEFAULT_BOOTSTRAP_TTL, _DEFAULT_BOOTSTRAP_TTL)
        payload = {
            "scope": "worker:bootstrap",
            "pool_id": pool_id,
            "iat": int(time.time()),
            "exp": int(time.time()) + ttl,
        }
        return jwt.encode(payload, self.secret_key, algorithm=self.algorithm)

    def mint_worker_token(
        self,
        *,
        node_id: str,
        pool_id: str,
        ttl_seconds: int | None = None,
    ) -> str:
        ttl = ttl_seconds or _DEFAULT_WORKER_TTL
        payload = {
            "sub": node_id,
            "kind": "worker",
            "pool_id": pool_id,
            "iat": int(time.time()),
            "exp": int(time.time()) + ttl,
        }
        return jwt.encode(payload, self.secret_key, algorithm=self.algorithm)

    # --- verify -------------------------------------------------------------

    def verify_bootstrap_token(self, token: str) -> BootstrapClaims:
        payload = self._decode(token)
        if payload.get("scope") != "worker:bootstrap":
            raise InvalidTokenError("not a worker:bootstrap token")
        pool_id = payload.get("pool_id")
        if not pool_id:
            raise InvalidTokenError("missing pool_id claim")
        return BootstrapClaims(
            pool_id=pool_id,
            scope="worker:bootstrap",
            exp=int(payload.get("exp", 0)),
        )

    def verify_worker_token(self, token: str) -> WorkerClaims:
        payload = self._decode(token)
        if payload.get("kind") != "worker":
            raise InvalidTokenError("not a worker token (kind != 'worker')")
        sub = payload.get("sub")
        pool_id = payload.get("pool_id")
        if not sub or not pool_id:
            raise InvalidTokenError("missing required claim (sub or pool_id)")
        return WorkerClaims(
            sub=sub,
            pool_id=pool_id,
            kind="worker",
            exp=int(payload.get("exp", 0)),
        )

    # --- internals ----------------------------------------------------------

    def _decode(self, token: str) -> dict:
        try:
            return jwt.decode(
                token,
                self.secret_key,
                algorithms=[self.algorithm],
            )
        except JWTError as e:
            raise InvalidTokenError(str(e))


__all__ = [
    "WorkerAuth",
    "BootstrapClaims",
    "WorkerClaims",
    "InvalidTokenError",
]

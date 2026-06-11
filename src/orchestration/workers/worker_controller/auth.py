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
    # DB-backed bootstrap token helpers
    "InvalidBootstrapToken",
    "BootstrapClaim",
    "mint_bootstrap_token",
    "consume_bootstrap_token",
]

# ---------------------------------------------------------------------------
# DB-backed bootstrap token helpers (asyncpg)
# ---------------------------------------------------------------------------
# These functions deal with the worker_bootstrap_tokens table created in
# migration 20260520.  The table schema is:
#   id (uuid), token_hash (text), pool_id (uuid), org_id (text),
#   expires_at (timestamptz), consumed_at (timestamptz nullable),
#   consumed_node_id (uuid nullable), created_at (timestamptz default now())
#
# Design notes:
# - Only the SHA-256 hash of the plaintext token is persisted; the plaintext
#   is returned exactly once to the caller for embedding in EC2 user-data.
# - consume_bootstrap_token uses an atomic UPDATE … WHERE consumed_at IS NULL
#   so two concurrent callers racing on the same token: exactly one wins.
# - org_id is typed as str (text in the schema), not UUID.
# ---------------------------------------------------------------------------

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID, uuid4

import asyncpg


class InvalidBootstrapToken(Exception):
    """Raised when a bootstrap token is unknown, already consumed, or expired."""


@dataclass(frozen=True)
class BootstrapClaim:
    bootstrap_id: UUID
    pool_id: UUID
    org_id: str  # org_id is text in the schema (migration 20260520), not uuid


DEFAULT_BOOTSTRAP_TTL_SECONDS = 3600


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def mint_bootstrap_token(
    conn: asyncpg.Connection,
    *,
    pool_id: UUID,
    org_id: str,
    ttl_seconds: int = DEFAULT_BOOTSTRAP_TTL_SECONDS,
) -> tuple[str, UUID]:
    """Generate a fresh URL-safe token, store its SHA-256 hash, return
    (plaintext_token, bootstrap_id).

    Negative TTL is allowed for tests: it produces a row whose expires_at is
    already in the past, which consume_bootstrap_token will reject.
    """
    token = secrets.token_urlsafe(32)
    bid = uuid4()
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)
    await conn.execute(
        """
        INSERT INTO worker_bootstrap_tokens (id, token_hash, pool_id, org_id, expires_at)
        VALUES ($1, $2, $3, $4, $5)
        """,
        bid,
        _hash_token(token),
        pool_id,
        org_id,
        expires_at,
    )
    return token, bid


async def consume_bootstrap_token(
    conn: asyncpg.Connection,
    *,
    token: str,
) -> BootstrapClaim:
    """Atomically consume a bootstrap token; return BootstrapClaim or raise
    InvalidBootstrapToken.

    The UPDATE … WHERE consumed_at IS NULL guarantees single-use even under
    concurrent callers: the database serialises the UPDATE and only one
    transaction can set consumed_at for a given row.
    """
    row = await conn.fetchrow(
        """
        UPDATE worker_bootstrap_tokens
        SET consumed_at = now()
        WHERE token_hash = $1
          AND consumed_at IS NULL
          AND expires_at > now()
        RETURNING id, pool_id, org_id
        """,
        _hash_token(token),
    )
    if row is None:
        raise InvalidBootstrapToken(
            "bootstrap token is unknown, already consumed, or expired"
        )
    return BootstrapClaim(
        bootstrap_id=row["id"],
        pool_id=row["pool_id"],
        org_id=row["org_id"],
    )

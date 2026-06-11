"""Tests for worker_controller.auth."""

import hashlib
import time
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest

from services.orchestration.worker_controller.auth import (
    BootstrapClaim,
    BootstrapClaims,
    InvalidBootstrapToken,
    InvalidTokenError,
    WorkerAuth,
    WorkerClaims,
    _hash_token,
    consume_bootstrap_token,
    mint_bootstrap_token,
)


SECRET = "test-secret-key-at-least-32-chars-long!"


def make_auth() -> WorkerAuth:
    return WorkerAuth(secret_key=SECRET, algorithm="HS256")


# ---------------------------------------------------------------------------
# Bootstrap tokens
# ---------------------------------------------------------------------------


class TestBootstrapTokens:
    def test_mint_and_verify(self):
        auth = make_auth()
        token = auth.mint_bootstrap_token(pool_id="pool-x", ttl_seconds=300)
        claims = auth.verify_bootstrap_token(token)
        assert claims.pool_id == "pool-x"
        assert claims.scope == "worker:bootstrap"

    def test_mint_default_ttl_under_24h(self):
        auth = make_auth()
        token = auth.mint_bootstrap_token(pool_id="p")
        claims = auth.verify_bootstrap_token(token)
        # Default TTL is at most 24h.
        assert claims.exp - int(time.time()) <= 24 * 3600 + 5

    def test_expired_bootstrap_rejected(self):
        # Use jose directly to forge a token that is already 60 seconds expired
        # so we avoid jose's default leeway window without sleeping in the test.
        from jose import jwt
        token = jwt.encode(
            {
                "scope": "worker:bootstrap",
                "pool_id": "p",
                "exp": int(time.time()) - 60,
                "iat": int(time.time()) - 120,
            },
            SECRET, algorithm="HS256",
        )
        auth = make_auth()
        with pytest.raises(InvalidTokenError):
            auth.verify_bootstrap_token(token)

    def test_tampered_bootstrap_rejected(self):
        auth = make_auth()
        token = auth.mint_bootstrap_token(pool_id="p")
        bad = token[:-2] + "AA"
        with pytest.raises(InvalidTokenError):
            auth.verify_bootstrap_token(bad)

    def test_worker_token_rejected_as_bootstrap(self):
        auth = make_auth()
        wjwt = auth.mint_worker_token(node_id="n", pool_id="p")
        with pytest.raises(InvalidTokenError):
            auth.verify_bootstrap_token(wjwt)


# ---------------------------------------------------------------------------
# Worker tokens
# ---------------------------------------------------------------------------


class TestWorkerTokens:
    def test_mint_and_verify(self):
        auth = make_auth()
        token = auth.mint_worker_token(node_id="node-uuid", pool_id="pool-x")
        claims = auth.verify_worker_token(token)
        assert isinstance(claims, WorkerClaims)
        assert claims.sub == "node-uuid"
        assert claims.pool_id == "pool-x"
        assert claims.kind == "worker"

    def test_default_ttl_is_30d(self):
        auth = make_auth()
        token = auth.mint_worker_token(node_id="n", pool_id="p")
        claims = auth.verify_worker_token(token)
        assert claims.exp - int(time.time()) > 25 * 24 * 3600  # well over 25d
        assert claims.exp - int(time.time()) <= 31 * 24 * 3600

    def test_expired_worker_rejected(self):
        from jose import jwt
        token = jwt.encode(
            {
                "sub": "n", "kind": "worker", "pool_id": "p",
                "exp": int(time.time()) - 60,
                "iat": int(time.time()) - 120,
            },
            SECRET, algorithm="HS256",
        )
        auth = make_auth()
        with pytest.raises(InvalidTokenError):
            auth.verify_worker_token(token)

    def test_wrong_secret_rejected(self):
        a1 = WorkerAuth(secret_key=SECRET, algorithm="HS256")
        a2 = WorkerAuth(secret_key="x" * 40, algorithm="HS256")
        token = a1.mint_worker_token(node_id="n", pool_id="p")
        with pytest.raises(InvalidTokenError):
            a2.verify_worker_token(token)

    def test_bootstrap_token_rejected_as_worker(self):
        auth = make_auth()
        boot = auth.mint_bootstrap_token(pool_id="p")
        with pytest.raises(InvalidTokenError):
            auth.verify_worker_token(boot)

    def test_user_jwt_rejected_as_worker(self):
        """A user-shaped token (no kind=worker claim) must not authenticate."""
        from jose import jwt
        bogus = jwt.encode(
            {"sub": "user", "type": "access"},  # no 'kind' claim
            SECRET, algorithm="HS256",
        )
        auth = make_auth()
        with pytest.raises(InvalidTokenError):
            auth.verify_worker_token(bogus)

    def test_unknown_algorithm_rejected(self):
        # Force a token signed with a different alg the verifier doesn't allow.
        from jose import jwt as jose_jwt
        token = jose_jwt.encode(
            {"sub": "n", "kind": "worker", "pool_id": "p", "exp": int(time.time()) + 3600},
            SECRET, algorithm="HS512",
        )
        auth = make_auth()  # HS256 only
        with pytest.raises(InvalidTokenError):
            auth.verify_worker_token(token)

    def test_missing_required_claim_rejected(self):
        from jose import jwt as jose_jwt
        token = jose_jwt.encode(
            {"sub": "n", "kind": "worker", "exp": int(time.time()) + 3600},  # no pool_id
            SECRET, algorithm="HS256",
        )
        auth = make_auth()
        with pytest.raises(InvalidTokenError):
            auth.verify_worker_token(token)


class TestSecretKeyValidation:
    def test_short_secret_rejected(self):
        with pytest.raises(ValueError):
            WorkerAuth(secret_key="too-short", algorithm="HS256")

    def test_unsupported_algorithm_rejected(self):
        with pytest.raises(ValueError):
            WorkerAuth(secret_key=SECRET, algorithm="none")  # disallowed alg


# ---------------------------------------------------------------------------
# DB-backed bootstrap token helpers (mock asyncpg.Connection)
#
# Rationale: the existing test infrastructure uses pure mocks throughout —
# no real database. We mirror that pattern here using AsyncMock to simulate
# an asyncpg.Connection.  The mock tracks every SQL call so we can assert
# the correct queries and parameters were issued.
#
# The race-condition test (exactly one winner under concurrent UPDATE) cannot
# be faithfully modelled with a single-threaded mock because the atomicity
# guarantee comes from the database engine, not from Python code.  It is
# therefore skipped and annotated accordingly.
# ---------------------------------------------------------------------------


def _make_mock_conn() -> AsyncMock:
    """Return a fresh AsyncMock that quacks like asyncpg.Connection."""
    conn = AsyncMock()
    # Default: execute succeeds, fetchrow returns None (no matching row)
    conn.execute = AsyncMock(return_value=None)
    conn.fetchrow = AsyncMock(return_value=None)
    return conn


def _pool_id() -> UUID:
    return uuid4()


def _org_id() -> str:
    return "org-test-" + uuid4().hex[:8]


class TestMintBootstrapToken:
    """Unit tests for mint_bootstrap_token using mocked asyncpg connection."""

    @pytest.mark.asyncio
    async def test_returns_non_empty_token_and_uuid(self):
        conn = _make_mock_conn()
        token, bid = await mint_bootstrap_token(
            conn, pool_id=_pool_id(), org_id=_org_id()
        )
        assert isinstance(token, str) and len(token) >= 32
        assert isinstance(bid, UUID)

    @pytest.mark.asyncio
    async def test_calls_execute_once(self):
        conn = _make_mock_conn()
        await mint_bootstrap_token(conn, pool_id=_pool_id(), org_id=_org_id())
        conn.execute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_inserts_hash_not_plaintext(self):
        conn = _make_mock_conn()
        pool = _pool_id()
        org = _org_id()
        token, _ = await mint_bootstrap_token(conn, pool_id=pool, org_id=org)
        # Inspect positional args of the INSERT call
        call_args = conn.execute.call_args
        sql, bid_arg, hash_arg, pool_arg, org_arg, expires_arg = (
            call_args.args[0],
            call_args.args[1],
            call_args.args[2],
            call_args.args[3],
            call_args.args[4],
            call_args.args[5],
        )
        expected_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        assert hash_arg == expected_hash, "token_hash must be SHA-256 of the plaintext"
        assert hash_arg != token, "plaintext token must not appear in token_hash arg"
        assert pool_arg == pool
        assert org_arg == org
        assert "INSERT" in sql.upper()

    @pytest.mark.asyncio
    async def test_two_mints_produce_distinct_tokens(self):
        conn = _make_mock_conn()
        pool = _pool_id()
        org = _org_id()
        t1, b1 = await mint_bootstrap_token(conn, pool_id=pool, org_id=org)
        t2, b2 = await mint_bootstrap_token(conn, pool_id=pool, org_id=org)
        assert t1 != t2
        assert b1 != b2

    @pytest.mark.asyncio
    async def test_token_length_at_least_32_chars(self):
        conn = _make_mock_conn()
        token, _ = await mint_bootstrap_token(
            conn, pool_id=_pool_id(), org_id=_org_id()
        )
        assert len(token) >= 32

    @pytest.mark.asyncio
    async def test_negative_ttl_still_calls_execute(self):
        """Negative TTL creates an already-expired row; execute still called."""
        conn = _make_mock_conn()
        token, bid = await mint_bootstrap_token(
            conn, pool_id=_pool_id(), org_id=_org_id(), ttl_seconds=-1
        )
        conn.execute.assert_awaited_once()
        assert isinstance(token, str)


class TestConsumeBootstrapToken:
    """Unit tests for consume_bootstrap_token using mocked asyncpg connection."""

    def _make_db_row(self, bid: UUID, pool: UUID, org: str) -> MagicMock:
        """Simulate an asyncpg Record returned by fetchrow."""
        row = MagicMock()
        row.__getitem__ = lambda self, key: {
            "id": bid,
            "pool_id": pool,
            "org_id": org,
        }[key]
        return row

    @pytest.mark.asyncio
    async def test_happy_path_returns_claim(self):
        bid = uuid4()
        pool = _pool_id()
        org = _org_id()
        conn = _make_mock_conn()
        conn.fetchrow = AsyncMock(return_value=self._make_db_row(bid, pool, org))

        claim = await consume_bootstrap_token(conn, token="some-token")

        assert claim.bootstrap_id == bid
        assert claim.pool_id == pool
        assert claim.org_id == org

    @pytest.mark.asyncio
    async def test_calls_fetchrow_with_correct_hash(self):
        token = "my-plaintext-token"
        conn = _make_mock_conn()
        conn.fetchrow = AsyncMock(
            return_value=self._make_db_row(uuid4(), _pool_id(), _org_id())
        )
        await consume_bootstrap_token(conn, token=token)
        call_args = conn.fetchrow.call_args
        sql = call_args.args[0]
        hash_arg = call_args.args[1]
        assert hash_arg == _hash_token(token)
        assert "UPDATE" in sql.upper()
        assert "consumed_at IS NULL" in sql

    @pytest.mark.asyncio
    async def test_unknown_token_raises(self):
        conn = _make_mock_conn()
        conn.fetchrow = AsyncMock(return_value=None)
        with pytest.raises(InvalidBootstrapToken):
            await consume_bootstrap_token(conn, token="not-a-real-token")

    @pytest.mark.asyncio
    async def test_already_consumed_token_raises(self):
        """Simulate what happens when the UPDATE matches 0 rows (row gone)."""
        conn = _make_mock_conn()
        conn.fetchrow = AsyncMock(return_value=None)
        with pytest.raises(InvalidBootstrapToken):
            await consume_bootstrap_token(conn, token="already-consumed")

    @pytest.mark.asyncio
    async def test_expired_token_raises(self):
        """Expired token: fetchrow returns None because expires_at <= now()."""
        conn = _make_mock_conn()
        conn.fetchrow = AsyncMock(return_value=None)
        with pytest.raises(InvalidBootstrapToken):
            await consume_bootstrap_token(conn, token="expired-token")

    @pytest.mark.asyncio
    async def test_returns_bootstrap_claim_dataclass(self):
        bid = uuid4()
        conn = _make_mock_conn()
        conn.fetchrow = AsyncMock(
            return_value=self._make_db_row(bid, _pool_id(), _org_id())
        )
        claim = await consume_bootstrap_token(conn, token="t")
        assert isinstance(claim, BootstrapClaim)

    @pytest.mark.asyncio
    async def test_claim_is_frozen(self):
        conn = _make_mock_conn()
        conn.fetchrow = AsyncMock(
            return_value=self._make_db_row(uuid4(), _pool_id(), _org_id())
        )
        claim = await consume_bootstrap_token(conn, token="t")
        with pytest.raises((AttributeError, TypeError)):
            claim.bootstrap_id = uuid4()  # type: ignore[misc]

    @pytest.mark.asyncio
    @pytest.mark.skip(
        reason=(
            "Atomicity of UPDATE…WHERE consumed_at IS NULL under concurrent "
            "callers is a database-engine guarantee and cannot be faithfully "
            "replicated with a mock asyncpg connection.  Use an integration "
            "test against a real PostgreSQL instance to validate this."
        )
    )
    async def test_race_only_one_wins(self):
        pass  # placeholder — covered by integration tests


class TestHashTokenHelper:
    """Unit tests for the internal _hash_token helper."""

    def test_sha256_hex_output(self):
        h = _hash_token("hello")
        assert h == hashlib.sha256(b"hello").hexdigest()

    def test_empty_string(self):
        h = _hash_token("")
        assert h == hashlib.sha256(b"").hexdigest()

    def test_unicode_token(self):
        tok = "éàü"  # accented chars
        h = _hash_token(tok)
        assert h == hashlib.sha256(tok.encode("utf-8")).hexdigest()

    def test_different_tokens_produce_different_hashes(self):
        assert _hash_token("abc") != _hash_token("xyz")

    def test_same_token_always_same_hash(self):
        t = "deterministic-token"
        assert _hash_token(t) == _hash_token(t)

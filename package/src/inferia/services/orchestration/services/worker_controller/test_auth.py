"""Tests for worker_controller.auth."""

import time

import pytest

from inferia.services.orchestration.services.worker_controller.auth import (
    BootstrapClaims,
    WorkerAuth,
    WorkerClaims,
    InvalidTokenError,
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

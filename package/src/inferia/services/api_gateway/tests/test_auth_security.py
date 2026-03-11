"""Tests for AuthService security properties."""

import pytest
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock
from jose import jwt

import bcrypt

from inferia.services.api_gateway.rbac.auth import AuthService
from inferia.services.api_gateway.db.models import User as DBUser


@pytest.fixture
def auth():
    """AuthService with deterministic config."""
    svc = AuthService.__new__(AuthService)
    svc.secret_key = "test-secret-key-for-unit-tests-only"
    svc.algorithm = "HS256"
    svc.access_token_expire_minutes = 30
    svc.refresh_token_expire_days = 7
    return svc


@pytest.fixture
def mock_user():
    user = MagicMock(spec=DBUser)
    user.id = "user-001"
    user.email = "test@inferia.com"
    user.default_org_id = "org-001"
    return user


class TestPasswordVerification:
    """Password hash verification security."""

    def test_correct_password_verifies(self, auth):
        hashed = bcrypt.hashpw(b"correct-password", bcrypt.gensalt()).decode("utf-8")
        assert auth.verify_password("correct-password", hashed) is True

    def test_wrong_password_fails(self, auth):
        hashed = bcrypt.hashpw(b"correct-password", bcrypt.gensalt()).decode("utf-8")
        assert auth.verify_password("wrong-password", hashed) is False

    def test_corrupted_hash_returns_false(self, auth):
        """Corrupted hash should return False, not raise."""
        assert auth.verify_password("any-password", "not-a-valid-hash") is False

    def test_password_hash_is_string(self, auth):
        """get_password_hash returns a string for DB storage."""
        hashed = auth.get_password_hash("test-password")
        assert isinstance(hashed, str)
        assert auth.verify_password("test-password", hashed) is True


class TestJWTTokens:
    """JWT token creation and validation."""

    def test_token_contains_correct_claims(self, auth, mock_user):
        token = auth.create_access_token(mock_user, org_id="org-001", role="admin")
        payload = jwt.decode(token, auth.secret_key, algorithms=[auth.algorithm])
        assert payload["sub"] == "user-001"
        assert payload["org_id"] == "org-001"
        assert payload["roles"] == ["admin"]
        assert payload["type"] == "access"
        assert "exp" in payload

    def test_expired_token_rejected(self, auth, mock_user):
        """Token with past expiry should raise on decode."""
        from jose import JWTError
        from fastapi import HTTPException

        # Create token that's already expired
        payload = {
            "sub": "user-001",
            "exp": datetime.now(timezone.utc) - timedelta(hours=1),
            "iat": datetime.now(timezone.utc) - timedelta(hours=2),
            "type": "access",
            "roles": ["admin"],
            "org_id": "org-001",
        }
        token = jwt.encode(payload, auth.secret_key, algorithm=auth.algorithm)
        with pytest.raises(HTTPException) as exc_info:
            auth.decode_token(token)
        assert exc_info.value.status_code == 401

    def test_tampered_signature_rejected(self, auth, mock_user):
        from fastapi import HTTPException

        token = auth.create_access_token(mock_user, org_id="org-001", role="admin")
        # Tamper with the token
        tampered = token[:-5] + "XXXXX"
        with pytest.raises(HTTPException) as exc_info:
            auth.decode_token(tampered)
        assert exc_info.value.status_code == 401

    def test_token_with_wrong_secret_rejected(self, auth, mock_user):
        from fastapi import HTTPException

        payload = {
            "sub": "user-001",
            "exp": datetime.now(timezone.utc) + timedelta(hours=1),
            "type": "access",
            "roles": ["admin"],
            "org_id": "org-001",
        }
        token = jwt.encode(payload, "different-secret", algorithm="HS256")
        with pytest.raises(HTTPException):
            auth.decode_token(token)

    def test_refresh_token_type(self, auth, mock_user):
        token = auth.create_refresh_token(mock_user, org_id="org-001")
        payload = jwt.decode(token, auth.secret_key, algorithms=[auth.algorithm])
        assert payload["type"] == "refresh"

    def test_access_token_type(self, auth, mock_user):
        token = auth.create_access_token(mock_user, org_id="org-001", role="member")
        decoded = auth.decode_token(token)
        assert decoded.type == "access"

    def test_decode_returns_all_claims(self, auth, mock_user):
        token = auth.create_access_token(mock_user, org_id="org-002", role="power_user")
        decoded = auth.decode_token(token)
        assert decoded.sub == "user-001"
        assert decoded.org_id == "org-002"
        assert decoded.roles == ["power_user"]
        assert decoded.type == "access"

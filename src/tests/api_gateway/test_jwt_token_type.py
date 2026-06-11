"""Tests for decode_token expected_type enforcement (issue #52)."""

import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock
from jose import jwt
from fastapi import HTTPException

from services.api_gateway.rbac.auth import AuthService
from services.api_gateway.db.models import User as DBUser


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
    user.email = "test@com"
    user.default_org_id = "org-001"
    return user


class TestDecodeTokenExpectedType:
    """decode_token should enforce token type when expected_type is provided."""

    def test_rejects_refresh_token_when_access_expected(self, auth, mock_user):
        """A refresh token must be rejected when expected_type='access'."""
        refresh_token = auth.create_refresh_token(mock_user, org_id="org-001")
        with pytest.raises(HTTPException) as exc_info:
            auth.decode_token(refresh_token, expected_type="access")
        assert exc_info.value.status_code == 401
        assert "expected access" in exc_info.value.detail.lower()

    def test_rejects_access_token_when_refresh_expected(self, auth, mock_user):
        """An access token must be rejected when expected_type='refresh'."""
        access_token = auth.create_access_token(mock_user, org_id="org-001", role="member")
        with pytest.raises(HTTPException) as exc_info:
            auth.decode_token(access_token, expected_type="refresh")
        assert exc_info.value.status_code == 401
        assert "expected refresh" in exc_info.value.detail.lower()

    def test_accepts_any_type_without_expected_type(self, auth, mock_user):
        """Without expected_type, decode_token accepts both access and refresh tokens."""
        access_token = auth.create_access_token(mock_user, org_id="org-001", role="admin")
        refresh_token = auth.create_refresh_token(mock_user, org_id="org-001")

        access_payload = auth.decode_token(access_token)
        assert access_payload.type == "access"

        refresh_payload = auth.decode_token(refresh_token)
        assert refresh_payload.type == "refresh"

    def test_accepts_matching_expected_type_access(self, auth, mock_user):
        """Access token passes when expected_type='access'."""
        access_token = auth.create_access_token(mock_user, org_id="org-001", role="admin")
        payload = auth.decode_token(access_token, expected_type="access")
        assert payload.type == "access"
        assert payload.sub == "user-001"

    def test_accepts_matching_expected_type_refresh(self, auth, mock_user):
        """Refresh token passes when expected_type='refresh'."""
        refresh_token = auth.create_refresh_token(mock_user, org_id="org-001")
        payload = auth.decode_token(refresh_token, expected_type="refresh")
        assert payload.type == "refresh"
        assert payload.sub == "user-001"

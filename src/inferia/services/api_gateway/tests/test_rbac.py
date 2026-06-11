"""Unit tests for RBAC functionality."""

import pytest
from unittest.mock import AsyncMock, patch

from inferia.services.api_gateway.rbac.auth import auth_service
from inferia.services.api_gateway.db.models import User as DBUser
from inferia.services.api_gateway.schemas.auth import AuthToken


def _make_mock_user():
    return DBUser(
        id="user_admin_001",
        email="admin@inferia.com",
        password_hash="hashed",
        default_org_id="org_default",
        totp_enabled=False,
    )


def _make_auth_token():
    """Create a valid AuthToken via auth_service so middleware can decode it."""
    user = _make_mock_user()
    access = auth_service.create_access_token(user, org_id="org_default", role="admin")
    refresh = auth_service.create_refresh_token(user, org_id="org_default")
    return AuthToken(
        access_token=access,
        refresh_token=refresh,
        token_type="bearer",
        expires_in=3600,
        organizations=[],
    )


@pytest.mark.asyncio
async def test_login_success(client):
    """Test successful login."""
    mock_token = _make_auth_token()

    mock_user = _make_mock_user()

    with (
        patch.object(
            auth_service, "authenticate_user",
            new_callable=AsyncMock, return_value=mock_user,
        ),
        patch.object(
            auth_service, "login",
            new_callable=AsyncMock, return_value=mock_token,
        ),
    ):
        response = await client.post(
            "/auth/login",
            json={"username": "admin@inferia.com", "password": "admin123"},
        )

    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert "refresh_token" in data
    assert data["token_type"] == "bearer"


@pytest.mark.asyncio
async def test_login_invalid_credentials(client):
    """Test login with invalid credentials."""
    with (
        patch.object(
            auth_service, "authenticate_user",
            new_callable=AsyncMock, return_value=None,
        ),
        patch.object(
            auth_service, "log_failed_login", new_callable=AsyncMock,
        ),
    ):
        response = await client.post(
            "/auth/login",
            json={"username": "admin", "password": "wrongpassword"},
        )

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_get_current_user(client, admin_token):
    """Test getting current user information."""
    response = await client.get(
        "/auth/me",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["user_id"] == "user_admin_001"
    assert "admin" in data["roles"]


@pytest.mark.asyncio
async def test_get_permissions(client, admin_token):
    """Test getting user permissions."""
    response = await client.get(
        "/auth/permissions",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert "permissions" in data
    assert "allowed_models" in data
    assert len(data["allowed_models"]) > 0


@pytest.mark.asyncio
async def test_unauthorized_access(client):
    """Test that endpoints require authentication."""
    response = await client.get("/auth/me")
    assert response.status_code == 401
    assert "detail" in response.json()

    response = await client.get("/auth/permissions")
    assert response.status_code == 401
    assert "detail" in response.json()


@pytest.mark.asyncio
async def test_invalid_token(client):
    """Test with invalid token."""
    response = await client.get(
        "/auth/me",
        headers={"Authorization": "Bearer invalid_token_here"},
    )
    assert response.status_code == 401
    assert "detail" in response.json()

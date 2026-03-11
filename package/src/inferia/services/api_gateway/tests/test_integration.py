"""Integration tests for the API Gateway request flow."""

import pytest
from unittest.mock import AsyncMock, patch

from inferia.services.api_gateway.rbac.auth import auth_service
from inferia.services.api_gateway.db.models import User as DBUser
from inferia.services.api_gateway.schemas.auth import AuthToken


@pytest.mark.asyncio
async def test_complete_flow_admin(client):
    """Test complete flow: login -> get user info -> get permissions."""
    user = DBUser(
        id="user_admin_001",
        email="admin@inferia.com",
        password_hash="hashed",
        default_org_id="org_default",
        totp_enabled=False,
    )
    access = auth_service.create_access_token(user, org_id="org_default", role="admin")
    mock_token = AuthToken(
        access_token=access,
        refresh_token="refresh",
        token_type="bearer",
        expires_in=3600,
        organizations=[],
    )

    with (
        patch.object(
            auth_service, "authenticate_user",
            new_callable=AsyncMock, return_value=user,
        ),
        patch.object(
            auth_service, "login",
            new_callable=AsyncMock, return_value=mock_token,
        ),
    ):
        login_response = await client.post(
            "/auth/login",
            json={"username": "admin@inferia.com", "password": "admin123"},
        )
        assert login_response.status_code == 200
        token = login_response.json()["access_token"]

    # 2. Get user info
    user_response = await client.get(
        "/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert user_response.status_code == 200
    assert user_response.json()["user_id"] == "user_admin_001"

    # 3. Get permissions
    perm_response = await client.get(
        "/auth/permissions",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert perm_response.status_code == 200
    assert "permissions" in perm_response.json()
    assert "allowed_models" in perm_response.json()


@pytest.mark.asyncio
async def test_request_headers_flow(client, admin_token):
    """Test that standard headers flow through the system."""
    custom_request_id = "test-123"
    custom_trace_id = "trace-456"

    response = await client.get(
        "/auth/permissions",
        headers={
            "Authorization": f"Bearer {admin_token}",
            "X-Request-ID": custom_request_id,
            "X-Trace-ID": custom_trace_id,
        },
    )

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == custom_request_id
    assert "X-Processing-Time-MS" in response.headers

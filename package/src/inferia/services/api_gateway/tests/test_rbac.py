"""Unit tests for RBAC functionality."""

import pytest
from fastapi.testclient import TestClient


@pytest.mark.asyncio
async def test_login_success(client):
    """Test successful login."""
    response = await client.post(
        "/auth/login",
        json={"username": "admin", "password": "admin123"}
    )
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert "refresh_token" in data
    assert data["token_type"] == "bearer"


@pytest.mark.asyncio
async def test_login_invalid_credentials(client):
    """Test login with invalid credentials."""
    response = await client.post(
        "/auth/login",
        json={"username": "admin", "password": "wrongpassword"}
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_get_current_user(client, admin_token):
    """Test getting current user information."""
    response = await client.get(
        "/auth/me",
        headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["username"] == "admin"
    assert "admin" in data["roles"]


@pytest.mark.asyncio
async def test_get_permissions(client, developer_token):
    """Test getting user permissions."""
    response = await client.get(
        "/auth/permissions",
        headers={"Authorization": f"Bearer {developer_token}"}
    )
    assert response.status_code == 200
    data = response.json()
    assert "permissions" in data
    assert "allowed_models" in data
    assert len(data["allowed_models"]) > 0


@pytest.mark.asyncio
async def test_model_access_control_admin(client, admin_token):
    """Test that admin can access all models."""
    # Admin should be able to access GPT-4
    response = await client.post(
        "/v1/completions",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "model": "gpt-4",
            "messages": [{"role": "user", "content": "Test"}]
        }
    )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_model_access_control_guest(client, guest_token):
    """Test that guest cannot access premium models."""
    # Guest should NOT be able to access GPT-4
    response = await client.post(
        "/v1/completions",
        headers={"Authorization": f"Bearer {guest_token}"},
        json={
            "model": "gpt-4",
            "messages": [{"role": "user", "content": "Test"}]
        }
    )
    assert response.status_code == 403
    assert "permission" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_model_access_control_standard_user(client, user_token):
    """Test that standard user can access allowed models."""
    # Standard user should be able to access GPT-3.5
    response = await client.post(
        "/v1/completions",
        headers={"Authorization": f"Bearer {user_token}"},
        json={
            "model": "gpt-3.5-turbo",
            "messages": [{"role": "user", "content": "Test"}]
        }
    )
    assert response.status_code == 200
    
    # But NOT GPT-4
    response = await client.post(
        "/v1/completions",
        headers={"Authorization": f"Bearer {user_token}"},
        json={
            "model": "gpt-4",
            "messages": [{"role": "user", "content": "Test"}]
        }
    )
    # Should be 403 since standard user doesn't have GPT-4 access
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_unauthorized_access(client):
    """Test that endpoints require authentication."""
    response = await client.get("/auth/me")
    assert response.status_code == 401
    assert "detail" in response.json()
    
    response = await client.get("/auth/permissions")
    assert response.status_code == 401
    assert "detail" in response.json()
    
    response = await client.get("/v1/models/allowed")
    assert response.status_code == 401
    assert "detail" in response.json()


@pytest.mark.asyncio
async def test_invalid_token(client):
    """Test with invalid token."""
    response = await client.get(
        "/auth/me",
        headers={"Authorization": "Bearer invalid_token_here"}
    )
    assert response.status_code == 401
    assert "detail" in response.json()

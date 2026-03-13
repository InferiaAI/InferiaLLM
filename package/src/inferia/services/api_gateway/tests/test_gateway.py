"""Unit tests for API Gateway functionality."""

import pytest


@pytest.mark.asyncio
async def test_health_check(client):
    """Test health check endpoint."""
    response = await client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert "version" in data


@pytest.mark.asyncio
async def test_root_endpoint(client):
    """Test root endpoint."""
    response = await client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert "service" in data
    assert "version" in data


@pytest.mark.asyncio
async def test_request_id_header(client, admin_token):
    """Test that X-Request-ID is generated or preserved."""
    # Without X-Request-ID
    response = await client.get(
        "/auth/me",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert "X-Request-ID" in response.headers

    # With custom X-Request-ID
    custom_id = "test-request-123"
    response = await client.get(
        "/auth/me",
        headers={
            "Authorization": f"Bearer {admin_token}",
            "X-Request-ID": custom_id,
        },
    )
    assert response.headers["X-Request-ID"] == custom_id


@pytest.mark.asyncio
async def test_processing_time_header(client, admin_token):
    """Test that X-Processing-Time-MS header is present."""
    response = await client.get(
        "/auth/me",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert "X-Processing-Time-MS" in response.headers
    processing_time = float(response.headers["X-Processing-Time-MS"])
    assert processing_time > 0


@pytest.mark.asyncio
async def test_completion_without_auth(client):
    """Test that authenticated endpoints require authentication."""
    response = await client.get("/auth/me")
    assert response.status_code == 401
    assert "detail" in response.json()

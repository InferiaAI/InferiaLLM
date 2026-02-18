"""Unit tests for API Gateway functionality."""

import pytest
from fastapi.testclient import TestClient


def test_health_check(client):
    """Test health check endpoint."""
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert "version" in data


def test_root_endpoint(client):
    """Test root endpoint."""
    response = client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert "service" in data
    assert "version" in data


def test_request_id_header(client, admin_token):
    """Test that X-Request-ID is generated or preserved."""
    # Without X-Request-ID
    response = client.get(
        "/auth/me",
        headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert "X-Request-ID" in response.headers
    
    # With custom X-Request-ID
    custom_id = "test-request-123"
    response = client.get(
        "/auth/me",
        headers={
            "Authorization": f"Bearer {admin_token}",
            "X-Request-ID": custom_id
        }
    )
    assert response.headers["X-Request-ID"] == custom_id


def test_processing_time_header(client, admin_token):
    """Test that X-Processing-Time-MS header is present."""
    response = client.get(
        "/auth/me",
        headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert "X-Processing-Time-MS" in response.headers
    processing_time = float(response.headers["X-Processing-Time-MS"])
    assert processing_time > 0


def test_list_models(client, admin_token):
    """Test list models endpoint."""
    response = client.get(
        "/v1/models",
        headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["object"] == "list"
    assert len(data["data"]) > 0
    assert any(model["id"] == "gpt-4" for model in data["data"])


def test_completion_endpoint(client, admin_token):
    """Test completion endpoint with admin user."""
    response = client.post(
        "/v1/completions",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "model": "gpt-4",
            "messages": [{"role": "user", "content": "Hello, world!"}],
            "temperature": 0.7
        }
    )
    assert response.status_code == 200
    data = response.json()
    assert data["object"] == "chat.completion"
    assert len(data["choices"]) > 0
    assert data["choices"][0]["message"]["role"] == "assistant"
    assert "usage" in data


def test_completion_without_auth(client):
    """Test that completion endpoint requires authentication."""
    response = client.post(
        "/v1/completions",
        json={
            "model": "gpt-4",
            "messages": [{"role": "user", "content": "Hello"}]
        }
    )
    # Should return 401 status code as JSON response
    assert response.status_code == 401
    assert "detail" in response.json()


def test_allowed_models_endpoint(client, developer_token):
    """Test allowed models endpoint."""
    response = client.get(
        "/v1/models/allowed",
        headers={"Authorization": f"Bearer {developer_token}"}
    )
    assert response.status_code == 200
    data = response.json()
    assert "allowed_models" in data
    assert isinstance(data["allowed_models"], list)

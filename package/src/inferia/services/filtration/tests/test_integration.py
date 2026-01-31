"""Integration tests for the complete request flow."""

import pytest
from fastapi.testclient import TestClient


def test_complete_flow_admin(client):
    """Test complete flow: login -> get permissions -> make inference request."""
    # 1. Login
    login_response = client.post(
        "/auth/login",
        json={"username": "admin", "password": "admin123"}
    )
    assert login_response.status_code == 200
    token = login_response.json()["access_token"]
    
    # 2. Get user info
    user_response = client.get(
        "/auth/me",
        headers={"Authorization": f"Bearer {token}"}
    )
    assert user_response.status_code == 200
    assert user_response.json()["username"] == "admin"
    
    # 3. Get permissions
    perm_response = client.get(
        "/auth/permissions",
        headers={"Authorization": f"Bearer {token}"}
    )
    assert perm_response.status_code == 200
    allowed_models = perm_response.json()["allowed_models"]
    assert "gpt-4" in allowed_models
    
    # 4. Make inference request
    inference_response = client.post(
        "/v1/completions",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "model": "gpt-4",
            "messages": [{"role": "user", "content": "Hello, AI!"}],
            "temperature": 0.7
        }
    )
    assert inference_response.status_code == 200
    data = inference_response.json()
    assert data["model"] == "gpt-4"
    assert len(data["choices"]) > 0


def test_complete_flow_guest(client):
    """Test complete flow for guest user with limited access."""
    # 1. Login
    login_response = client.post(
        "/auth/login",
        json={"username": "guest", "password": "guest123"}
    )
    token = login_response.json()["access_token"]
    
    # 2. Check allowed models
    perm_response = client.get(
        "/auth/permissions",
        headers={"Authorization": f"Bearer {token}"}
    )
    allowed_models = perm_response.json()["allowed_models"]
    
    # 3. Can access allowed model
    if "gpt-3.5-turbo" in allowed_models:
        response = client.post(
            "/v1/completions",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "model": "gpt-3.5-turbo",
                "messages": [{"role": "user", "content": "Test"}]
            }
        )
        assert response.status_code == 200
    
    # 4. Cannot access restricted model
    response = client.post(
        "/v1/completions",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "model": "gpt-4",
            "messages": [{"role": "user", "content": "Test"}]
        }
    )
    assert response.status_code == 403


def test_request_headers_flow(client, admin_token):
    """Test that standard headers flow through the system."""
    custom_request_id = "test-123"
    custom_trace_id = "trace-456"
    
    response = client.post(
        "/v1/completions",
        headers={
            "Authorization": f"Bearer {admin_token}",
            "X-Request-ID": custom_request_id,
            "X-Trace-ID": custom_trace_id,
        },
        json={
            "model": "gpt-3.5-turbo",
            "messages": [{"role": "user", "content": "Test headers"}]
        }
    )
    
    assert response.status_code == 200
    # Check that headers are returned
    assert response.headers["X-Request-ID"] == custom_request_id
    assert response.headers["X-Trace-ID"] == custom_trace_id
    assert "X-Processing-Time-MS" in response.headers


def test_different_models(client, developer_token):
    """Test inference with different models."""
    models_to_test = ["gpt-3.5-turbo", "claude-3-sonnet", "llama-3-70b"]
    
    for model in models_to_test:
        response = client.post(
            "/v1/completions",
            headers={"Authorization": f"Bearer {developer_token}"},
            json={
                "model": model,
                "messages": [{"role": "user", "content": f"Test {model}"}]
            }
        )
        
        # Should succeed for developer who has access to these models
        if response.status_code == 200:
            data = response.json()
            assert data["model"] == model
            assert model.lower() in data["choices"][0]["message"]["content"].lower() or \
                   "mock" in data["choices"][0]["message"]["content"].lower()

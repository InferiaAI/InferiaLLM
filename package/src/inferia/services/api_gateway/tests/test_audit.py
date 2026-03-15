"""Unit tests for audit functionality."""

import pytest
from unittest.mock import patch, AsyncMock
from datetime import datetime
import uuid


VALID_INTERNAL_KEY = "a" * 32  # 32-char key that satisfies min_length


@pytest.mark.asyncio
async def test_audit_flow_admin(client, admin_token):
    """Test full audit flow: Log event via internal endpoint -> Retrieve logs."""
    mock_id = str(uuid.uuid4())
    mock_log = {
        "id": mock_id,
        "timestamp": datetime.now().isoformat(),
        "user_id": "test_user_123",
        "action": "test_action",
        "resource_type": "model",
        "resource_id": "gpt-4",
        "details": {"foo": "bar"},
        "ip_address": "127.0.0.1",
        "status": "success",
    }

    with (
        patch(
            "inferia.services.api_gateway.audit.router.settings"
        ) as mock_settings,
        patch(
            "inferia.services.api_gateway.audit.router.audit_service"
        ) as mock_service,
    ):
        mock_settings.internal_api_key = VALID_INTERNAL_KEY
        mock_service.log_event = AsyncMock(return_value=mock_log)
        mock_service.get_logs = AsyncMock(return_value=[mock_log])

        # 1. Create a log entry via internal endpoint (requires API key, not Bearer)
        log_data = {
            "user_id": "test_user_123",
            "action": "test_action",
            "resource_type": "model",
            "resource_id": "gpt-4",
            "details": {"foo": "bar"},
            "ip_address": "127.0.0.1",
            "status": "success",
        }

        create_response = await client.post(
            "/audit/internal/log",
            headers={"X-Internal-API-Key": VALID_INTERNAL_KEY},
            json=log_data,
        )
        assert create_response.status_code == 200
        created_log = create_response.json()
        assert created_log["action"] == "test_action"
        assert created_log["id"] == mock_id

        # 2. Retrieve logs as Admin (requires Bearer token + audit permission)
        get_response = await client.get(
            "/audit/logs",
            headers={"Authorization": f"Bearer {admin_token}"},
            params={"action": "test_action"},
        )
        assert get_response.status_code == 200
        logs = get_response.json()
        assert len(logs) >= 1
        assert logs[0]["id"] == mock_id


@pytest.mark.asyncio
async def test_audit_access_denied_unauthenticated(client):
    """Test unauthenticated user cannot access audit logs."""
    response = await client.get("/audit/logs")
    assert response.status_code == 401

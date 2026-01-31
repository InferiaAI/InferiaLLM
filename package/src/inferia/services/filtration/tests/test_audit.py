import pytest
from audit.api_models import AuditLogCreate

from audit.api_models import AuditLogCreate
from unittest.mock import patch, AsyncMock
from datetime import datetime
import uuid

@pytest.mark.asyncio
async def test_audit_flow_admin(client, admin_token):
    """Test full audit flow: Log event -> Retrieve logs."""
    
    # Mock data to return
    mock_id = str(uuid.uuid4())
    mock_log = {
        "id": mock_id,
        "timestamp": datetime.now(),
        "user_id": "test_user_123",
        "action": "test_action",
        "resource_type": "model",
        "resource_id": "gpt-4",
        "details": {"foo": "bar"},
        "ip_address": "127.0.0.1", 
        "status": "success"
    }

    # Patch the service instance used in router
    with patch("audit.router.audit_service") as mock_service:
        # Configure mocks
        mock_service.log_event = AsyncMock(return_value=mock_log)
        mock_service.get_logs = AsyncMock(return_value=[mock_log])

        # 1. Create a log entry
        log_data = {
            "user_id": "test_user_123",
            "action": "test_action",
            "resource_type": "model",
            "resource_id": "gpt-4",
            "details": {"foo": "bar"},
            "ip_address": "127.0.0.1", 
            "status": "success"
        }
        
        create_response = await client.post(
            "/audit/internal/log",
            headers={"Authorization": f"Bearer {admin_token}"},
            json=log_data
        )
        assert create_response.status_code == 200
        created_log = create_response.json()
        assert created_log["action"] == "test_action"
        assert created_log["id"] == mock_id
        
        # 2. Retrieve logs as Admin
        get_response = await client.get(
            "/audit/logs",
            headers={"Authorization": f"Bearer {admin_token}"},
            params={"action": "test_action"}
        )
        assert get_response.status_code == 200
        logs = get_response.json()
        assert len(logs) >= 1
        assert logs[0]["id"] == mock_id

@pytest.mark.asyncio
async def test_audit_access_denied(client, guest_token):
    """Test standard user cannot access audit logs."""
    response = await client.get(
        "/audit/logs",
        headers={"Authorization": f"Bearer {guest_token}"}
    )
    assert response.status_code == 403

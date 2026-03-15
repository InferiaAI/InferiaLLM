"""Security tests for audit internal endpoint API key handling."""

import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from datetime import datetime
import uuid


VALID_INTERNAL_KEY = "a" * 32  # 32-char key that satisfies min_length

LOG_DATA = {
    "user_id": "test_user_123",
    "action": "test_action",
    "resource_type": "model",
    "resource_id": "gpt-4",
    "details": {"foo": "bar"},
    "ip_address": "127.0.0.1",
    "status": "success",
}


@pytest.mark.asyncio
async def test_rejects_when_internal_key_not_configured(client):
    """Endpoint must return 503 when settings.internal_api_key is None."""
    with patch(
        "inferia.services.api_gateway.audit.router.settings"
    ) as mock_settings:
        mock_settings.internal_api_key = None

        response = await client.post(
            "/audit/internal/log",
            headers={"X-Internal-API-Key": "any-key-value-here"},
            json=LOG_DATA,
        )
        assert response.status_code == 503, (
            f"Expected 503 when internal_api_key is None, got {response.status_code}"
        )
        assert "not configured" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_rejects_wrong_key(client):
    """Endpoint must return 403 when the provided key does not match."""
    with patch(
        "inferia.services.api_gateway.audit.router.settings"
    ) as mock_settings:
        mock_settings.internal_api_key = VALID_INTERNAL_KEY

        response = await client.post(
            "/audit/internal/log",
            headers={"X-Internal-API-Key": "wrong-key"},
            json=LOG_DATA,
        )
        assert response.status_code == 403, (
            f"Expected 403 for wrong key, got {response.status_code}"
        )


@pytest.mark.asyncio
async def test_rejects_dev_internal_key(client):
    """The static 'dev-internal-key' must NOT be accepted."""
    with patch(
        "inferia.services.api_gateway.audit.router.settings"
    ) as mock_settings:
        mock_settings.internal_api_key = VALID_INTERNAL_KEY

        response = await client.post(
            "/audit/internal/log",
            headers={"X-Internal-API-Key": "dev-internal-key"},
            json=LOG_DATA,
        )
        assert response.status_code == 403, (
            f"Expected 403 for dev-internal-key, got {response.status_code}"
        )


@pytest.mark.asyncio
async def test_accepts_valid_key(client):
    """Endpoint must accept the correct key and return 200."""
    mock_id = str(uuid.uuid4())
    mock_log = {
        "id": mock_id,
        "timestamp": datetime.now().isoformat(),
        **LOG_DATA,
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

        response = await client.post(
            "/audit/internal/log",
            headers={"X-Internal-API-Key": VALID_INTERNAL_KEY},
            json=LOG_DATA,
        )
        assert response.status_code == 200, (
            f"Expected 200 for valid key, got {response.status_code}"
        )

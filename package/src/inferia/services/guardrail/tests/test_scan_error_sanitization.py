"""Tests for error sanitization in guardrail scan endpoint (issue #47).

Internal error details (stack frames, file paths, connection strings)
must NOT be exposed to API consumers via HTTPException detail.
"""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi import HTTPException

from inferia.services.guardrail.app import scan


@pytest.mark.asyncio
async def test_scan_error_does_not_expose_internal_details():
    """When scan raises an internal error, the HTTPException detail must be generic."""
    internal_msg = "psycopg2.OperationalError: connection to server at 10.0.0.5 refused"

    request = MagicMock()
    request.text = "test input"
    request.scan_type = "input"
    request.user_id = None
    request.custom_banned_keywords = []
    request.pii_entities = []
    request.config = {}
    request.context = ""

    with patch(
        "inferia.services.guardrail.app.guardrail_engine"
    ) as mock_engine:
        mock_engine.scan_input = AsyncMock(
            side_effect=Exception(internal_msg)
        )

        with pytest.raises(HTTPException) as exc_info:
            await scan(request)

        assert exc_info.value.status_code == 500
        assert internal_msg not in exc_info.value.detail, \
            f"Internal error exposed: {exc_info.value.detail}"

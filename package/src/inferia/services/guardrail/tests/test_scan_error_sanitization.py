"""Tests for error sanitization in guardrail scan endpoint (issue #47).

Internal error details (stack frames, file paths, connection strings)
must NOT be exposed to API consumers via HTTPException detail.
"""

import os
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from httpx import AsyncClient, ASGITransport


TEST_API_KEY = "test-key-for-unit-tests"


@pytest.mark.asyncio
async def test_scan_error_does_not_expose_internal_details():
    """When scan raises an internal error, the response must not contain str(e)."""
    internal_msg = "psycopg2.OperationalError: connection to server at 10.0.0.5 refused"

    with patch.dict(os.environ, {"INTERNAL_API_KEY": TEST_API_KEY}):
        # Import after env is set so settings pick it up
        from importlib import reload
        import inferia.services.guardrail.config as cfg
        reload(cfg)
        import inferia.services.guardrail.app as app_mod
        reload(app_mod)

        with patch.object(
            app_mod, "guardrail_engine"
        ) as mock_engine:
            mock_engine.scan_input = AsyncMock(
                side_effect=Exception(internal_msg)
            )

            transport = ASGITransport(app=app_mod.app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/scan",
                    json={
                        "text": "test input",
                        "scan_type": "input",
                    },
                    headers={"X-Internal-API-Key": TEST_API_KEY},
                )

            assert resp.status_code == 500
            body = resp.json()
            detail = body.get("detail", "")
            assert internal_msg not in detail, \
                f"Internal error exposed to consumer: {detail}"

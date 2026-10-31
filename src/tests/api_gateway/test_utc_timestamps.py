"""Timestamps leave the API with a UTC offset (#447).

Columns are timestamp without time zone holding UTC. Sent without an offset,
browsers read them as local time and the dashboard shifts them by the viewer's
UTC offset.
"""

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import BaseModel

from api_gateway.schemas.common import UTCDateTime
from api_gateway.schemas.logging import AuditLogResponse, InferenceLogResponse

NAIVE_UTC = datetime(2026, 10, 27, 8, 37, 11, 464961)


class _Stamped(BaseModel):
    at: UTCDateTime


def test_naive_value_is_sent_as_utc():
    assert _Stamped(at=NAIVE_UTC).model_dump(mode="json")["at"] == (
        "2026-10-27T08:37:11.464961+00:00"
    )


def test_aware_value_keeps_its_offset():
    ist = timezone(timedelta(hours=5, minutes=30))
    at = datetime(2026, 10, 27, 14, 7, 11, tzinfo=ist)
    assert _Stamped(at=at).model_dump(mode="json")["at"] == "2026-10-27T14:07:11+05:30"


def test_python_dump_keeps_the_datetime():
    """Only JSON output changes; code using model_dump() still gets a datetime."""
    assert _Stamped(at=NAIVE_UTC).model_dump()["at"] == NAIVE_UTC


def test_audit_log_timestamp_has_offset():
    log = AuditLogResponse(
        id="a1", timestamp=NAIVE_UTC, user_id="u1", action="user.login",
        resource_type=None, resource_id=None, details=None, ip_address=None,
        status="success",
    )
    assert log.model_dump_json().count("+00:00") == 1
    assert log.model_dump(mode="json")["timestamp"].endswith("+00:00")


def test_inference_log_created_at_has_offset():
    log = InferenceLogResponse(
        id="l1", deployment_id="d1", user_id="u1", model="m",
        prompt_tokens=1, completion_tokens=1, total_tokens=2, status_code=200,
        is_streaming=False, created_at=NAIVE_UTC,
    )
    assert log.model_dump(mode="json")["created_at"].endswith("+00:00")


@pytest.mark.asyncio
async def test_audit_logs_endpoint_sends_offset(client, admin_token):
    """End to end through the route's response_model, as the dashboard sees it."""
    stored = {
        "id": str(uuid.uuid4()),
        "timestamp": NAIVE_UTC,
        "user_id": "u1",
        "action": "user.login",
        "resource_type": None,
        "resource_id": None,
        "details": None,
        "ip_address": None,
        "status": "success",
    }
    with patch("api_gateway.audit.router.audit_service") as service:
        service.get_logs = AsyncMock(return_value=[stored])
        resp = await client.get(
            "/audit/logs", headers={"Authorization": f"Bearer {admin_token}"},
        )
    assert resp.status_code == 200, resp.text
    assert resp.json()[0]["timestamp"] == "2026-10-27T08:37:11.464961+00:00"

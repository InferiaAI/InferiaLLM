"""Tests for org_id filtering in audit log queries (issue #44)."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime

from inferia.services.api_gateway.audit.service import AuditService
from inferia.services.api_gateway.models import AuditLogFilter, UserContext, PermissionEnum


@pytest.mark.asyncio
async def test_get_logs_filters_by_org_id():
    """Test that get_logs applies org_id WHERE clause when filter has org_id."""
    service = AuditService()

    mock_db = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []
    mock_db.execute.return_value = mock_result

    filters = AuditLogFilter(org_id="org-123")

    await service.get_logs(mock_db, filters)

    # Inspect the query passed to db.execute
    call_args = mock_db.execute.call_args
    query_str = str(call_args[0][0])
    assert "WHERE" in query_str and "org_id" in query_str.split("WHERE")[1], (
        "Expected org_id in WHERE clause, got: " + query_str
    )


@pytest.mark.asyncio
async def test_get_logs_without_org_id_no_org_filter():
    """Test that get_logs does NOT add org_id clause when org_id is None."""
    service = AuditService()

    mock_db = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []
    mock_db.execute.return_value = mock_result

    filters = AuditLogFilter(org_id=None)

    await service.get_logs(mock_db, filters)

    call_args = mock_db.execute.call_args
    query_str = str(call_args[0][0])
    # org_id appears in the SELECT column list because it is a model column,
    # but there should be no WHERE clause referencing it.
    if "WHERE" in query_str:
        assert "org_id" not in query_str.split("WHERE")[1], (
            "Did not expect org_id in WHERE clause when org_id is None, got: "
            + query_str
        )


@pytest.mark.asyncio
async def test_router_passes_user_org_id_to_filter():
    """Test that the audit router injects user_ctx.org_id into the filter."""
    from inferia.services.api_gateway.audit.router import get_audit_logs

    mock_db = AsyncMock()

    user_ctx = UserContext(
        user_id="user-1",
        username="testuser",
        email="test@example.com",
        roles=["admin"],
        permissions=[PermissionEnum.AUDIT_LOG_LIST.value],
        org_id="org-456",
        quota_limit=10000,
        quota_used=0,
    )

    with (
        patch(
            "inferia.services.api_gateway.audit.router.audit_service"
        ) as mock_audit_svc,
        patch(
            "inferia.services.api_gateway.audit.router.authz_service"
        ) as mock_authz_svc,
    ):
        mock_authz_svc.require_permission.return_value = None
        mock_audit_svc.get_logs = AsyncMock(return_value=[])

        await get_audit_logs(
            user_id=None,
            action=None,
            start_date=None,
            end_date=None,
            limit=100,
            skip=0,
            db=mock_db,
            user_ctx=user_ctx,
        )

        # Verify get_logs was called with a filter that has org_id="org-456"
        call_args = mock_audit_svc.get_logs.call_args
        filters_arg = call_args[0][1]
        assert filters_arg.org_id == "org-456", (
            f"Expected org_id='org-456' in filter, got org_id='{filters_arg.org_id}'"
        )

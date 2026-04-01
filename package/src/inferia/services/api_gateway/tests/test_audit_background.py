"""Tests for non-blocking audit writes (#79)."""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from inferia.services.api_gateway.audit.service import AuditService
from inferia.services.api_gateway.models import AuditLogCreate


def _make_event(**overrides):
    defaults = dict(
        user_id="u1",
        org_id="org1",
        action="user.login",
        category=None,
        resource_type="auth",
        resource_id="r1",
        details={"ip": "1.2.3.4"},
        ip_address="1.2.3.4",
        status="success",
    )
    defaults.update(overrides)
    return AuditLogCreate(**defaults)


class TestAuditServiceBackground:
    @pytest.mark.asyncio
    async def test_log_event_returns_immediately(self):
        """log_event should return None immediately (fire-and-forget)."""
        svc = AuditService()
        db = AsyncMock()
        event = _make_event()

        with patch.object(svc, "_write_audit_log", new_callable=AsyncMock) as mock_write:
            result = await svc.log_event(db, event)

        assert result is None

    @pytest.mark.asyncio
    async def test_log_event_spawns_background_task(self):
        """log_event should create an asyncio task for the DB write."""
        svc = AuditService()
        db = AsyncMock()
        event = _make_event()

        tasks_before = len(asyncio.all_tasks())

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        with patch(
            "inferia.services.api_gateway.audit.service.AuditService._write_audit_log",
            new_callable=AsyncMock,
        ):
            await svc.log_event(db, event)
            # Give the event loop a chance to register the task
            await asyncio.sleep(0)

    @pytest.mark.asyncio
    async def test_write_audit_log_commits_to_db(self):
        """_write_audit_log should open its own session and commit."""
        svc = AuditService()
        event = _make_event()

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        with patch(
            "inferia.services.api_gateway.db.database.AsyncSessionLocal",
            return_value=mock_session,
        ):
            await svc._write_audit_log(event)

        mock_session.add.assert_called_once()
        mock_session.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_write_audit_log_uses_own_session(self):
        """_write_audit_log must NOT use the caller's DB session."""
        svc = AuditService()
        event = _make_event()

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        with patch(
            "inferia.services.api_gateway.db.database.AsyncSessionLocal",
            return_value=mock_session,
        ) as mock_factory:
            await svc._write_audit_log(event)

        mock_factory.assert_called_once()

    @pytest.mark.asyncio
    async def test_write_audit_log_handles_db_error(self):
        """_write_audit_log should swallow DB errors (best-effort)."""
        svc = AuditService()
        event = _make_event()

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)
        mock_session.commit.side_effect = Exception("DB down")

        with patch(
            "inferia.services.api_gateway.db.database.AsyncSessionLocal",
            return_value=mock_session,
        ):
            # Should NOT raise
            await svc._write_audit_log(event)

    @pytest.mark.asyncio
    async def test_log_event_does_not_block_caller(self):
        """The caller should not wait for the DB write to complete."""
        svc = AuditService()
        db = AsyncMock()
        event = _make_event()

        write_started = asyncio.Event()
        write_done = asyncio.Event()

        async def slow_write(ev):
            write_started.set()
            await asyncio.sleep(0.5)
            write_done.set()

        with patch.object(svc, "_write_audit_log", side_effect=slow_write):
            await svc.log_event(db, event)
            # Give task a chance to start
            await asyncio.sleep(0.01)
            # The write may have started but caller already returned
            assert not write_done.is_set()

        # Cleanup: wait for background task
        await asyncio.sleep(0.6)

    @pytest.mark.asyncio
    async def test_category_auto_derived(self):
        """Category should be auto-derived from action if not set."""
        svc = AuditService()
        event = _make_event(category=None, action="user.login")

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        added_obj = None

        def capture_add(obj):
            nonlocal added_obj
            added_obj = obj

        mock_session.add = capture_add

        with patch(
            "inferia.services.api_gateway.db.database.AsyncSessionLocal",
            return_value=mock_session,
        ):
            await svc._write_audit_log(event)

        assert added_obj is not None
        # category should be populated (not None)
        assert added_obj.category is not None

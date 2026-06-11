"""Tests for background inference log persistence (#95).

Verifies that _persist_log_background creates its own DB session
instead of using the request-scoped one (which FastAPI closes
before the background task executes).
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from inferia.services.api_gateway.models import InferenceLogCreate


def _make_log_data(**overrides):
    defaults = dict(
        deployment_id="dep-1",
        user_id="user-1",
        ip_address="1.2.3.4",
        model="gpt-4",
        request_payload={"messages": [{"role": "user", "content": "hi"}]},
        latency_ms=100,
        ttft_ms=20,
        tokens_per_second=50.0,
        prompt_tokens=5,
        completion_tokens=10,
        total_tokens=15,
        status_code=200,
        error_message=None,
        is_streaming=False,
        applied_policies=["rate_limit"],
    )
    defaults.update(overrides)
    return InferenceLogCreate(**defaults)


class TestPersistLogBackground:
    @pytest.mark.asyncio
    async def test_creates_own_session(self):
        """Background task must create its own AsyncSessionLocal, not reuse caller's."""
        from inferia.services.api_gateway.gateway.router import _persist_log_background

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        with patch(
            "inferia.services.api_gateway.db.database.AsyncSessionLocal",
            return_value=mock_session,
        ) as factory:
            await _persist_log_background(_make_log_data(), "log-123")

        factory.assert_called_once()
        mock_session.add.assert_called_once()
        mock_session.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_does_not_accept_db_parameter(self):
        """Function signature must NOT accept a db parameter."""
        from inferia.services.api_gateway.gateway.router import _persist_log_background
        import inspect

        sig = inspect.signature(_persist_log_background)
        param_names = list(sig.parameters.keys())
        assert "db" not in param_names

    @pytest.mark.asyncio
    async def test_swallows_db_errors(self):
        """DB errors must be caught, not propagated to the caller."""
        from inferia.services.api_gateway.gateway.router import _persist_log_background

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)
        mock_session.commit.side_effect = Exception("connection closed")

        with patch(
            "inferia.services.api_gateway.db.database.AsyncSessionLocal",
            return_value=mock_session,
        ):
            # Should NOT raise
            await _persist_log_background(_make_log_data(), "log-err")

    @pytest.mark.asyncio
    async def test_encrypts_payload_when_available(self):
        """Payload should be encrypted when encryption service is available."""
        from inferia.services.api_gateway.gateway import router

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        added_obj = None

        def capture_add(obj):
            nonlocal added_obj
            added_obj = obj

        mock_session.add = capture_add

        mock_enc = MagicMock()
        mock_enc.encrypt.return_value = "encrypted_blob"

        orig_available = router.encryption_available
        orig_service = router.encryption_service
        try:
            router.encryption_available = True
            router.encryption_service = mock_enc

            with patch(
                "inferia.services.api_gateway.db.database.AsyncSessionLocal",
                return_value=mock_session,
            ):
                await router._persist_log_background(
                    _make_log_data(), "log-enc"
                )
        finally:
            router.encryption_available = orig_available
            router.encryption_service = orig_service

        assert added_obj is not None
        assert added_obj.request_payload["encrypted"] is True
        mock_enc.encrypt.assert_called_once()

    @pytest.mark.asyncio
    async def test_handles_none_payload(self):
        """None payload should be stored as None."""
        from inferia.services.api_gateway.gateway.router import _persist_log_background

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
            await _persist_log_background(
                _make_log_data(request_payload=None), "log-nil"
            )

        assert added_obj is not None
        assert added_obj.request_payload is None


class TestCreateInferenceLogEndpoint:
    @pytest.mark.asyncio
    async def test_endpoint_does_not_inject_db_into_background_task(self):
        """The endpoint must not pass db to background task."""
        from inferia.services.api_gateway.gateway.router import create_inference_log
        import inspect

        sig = inspect.signature(create_inference_log)
        param_names = list(sig.parameters.keys())
        # Endpoint should NOT have a db dependency anymore
        assert "db" not in param_names

    @pytest.mark.asyncio
    async def test_endpoint_returns_log_id(self):
        """Endpoint should return ok status and a log_id."""
        from inferia.services.api_gateway.gateway.router import create_inference_log
        from fastapi import BackgroundTasks

        bg = BackgroundTasks()
        result = await create_inference_log(
            log_data=_make_log_data(),
            background_tasks=bg,
        )

        assert result["status"] == "ok"
        assert "log_id" in result
        assert len(result["log_id"]) == 36  # UUID length

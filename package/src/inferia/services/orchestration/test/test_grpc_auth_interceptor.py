"""
Tests for gRPC server authentication interceptor.

Verifies that the InternalAPIKeyInterceptor:
1. Rejects calls when no API key is configured (fail closed)
2. Rejects calls with missing or invalid API key metadata
3. Allows calls with valid API key metadata
"""

import pytest
from unittest.mock import AsyncMock, MagicMock
import grpc

from inferia.services.orchestration.grpc_auth_interceptor import (
    InternalAPIKeyInterceptor,
)


def _make_handler_call_details(method="/test.Service/Method", metadata=None):
    """Create a mock HandlerCallDetails with given metadata."""
    details = MagicMock()
    details.method = method
    details.invocation_metadata = metadata or []
    return details


class TestGRPCAuthInterceptor:
    """Verify gRPC auth interceptor behavior."""

    @pytest.mark.asyncio
    async def test_missing_key_returns_unauthenticated(self):
        """Calls without x-internal-api-key metadata must be rejected."""
        interceptor = InternalAPIKeyInterceptor("valid-key")
        continuation = AsyncMock()
        details = _make_handler_call_details(metadata=[])

        handler = await interceptor.intercept_service(continuation, details)

        # continuation should NOT have been called
        continuation.assert_not_called()

        # The returned handler should abort with UNAUTHENTICATED
        context = AsyncMock()
        await handler.unary_unary(None, context)
        context.abort.assert_called_once()
        assert context.abort.call_args[0][0] == grpc.StatusCode.UNAUTHENTICATED

    @pytest.mark.asyncio
    async def test_invalid_key_returns_permission_denied(self):
        """Calls with wrong API key must be rejected."""
        interceptor = InternalAPIKeyInterceptor("valid-key")
        continuation = AsyncMock()
        details = _make_handler_call_details(
            metadata=[("x-internal-api-key", "wrong-key")]
        )

        handler = await interceptor.intercept_service(continuation, details)

        continuation.assert_not_called()

        context = AsyncMock()
        await handler.unary_unary(None, context)
        context.abort.assert_called_once()
        assert context.abort.call_args[0][0] == grpc.StatusCode.PERMISSION_DENIED

    @pytest.mark.asyncio
    async def test_valid_key_passes_through(self):
        """Calls with correct API key must reach the service handler."""
        interceptor = InternalAPIKeyInterceptor("valid-key")
        expected_handler = MagicMock()
        continuation = AsyncMock(return_value=expected_handler)
        details = _make_handler_call_details(
            metadata=[("x-internal-api-key", "valid-key")]
        )

        handler = await interceptor.intercept_service(continuation, details)

        # continuation should have been called (request passed through)
        continuation.assert_called_once_with(details)
        assert handler == expected_handler

    @pytest.mark.asyncio
    async def test_unconfigured_key_returns_unavailable(self):
        """When server has no API key configured, all calls must be rejected."""
        interceptor = InternalAPIKeyInterceptor("")
        continuation = AsyncMock()
        details = _make_handler_call_details(
            metadata=[("x-internal-api-key", "any-key")]
        )

        handler = await interceptor.intercept_service(continuation, details)

        continuation.assert_not_called()

        context = AsyncMock()
        await handler.unary_unary(None, context)
        context.abort.assert_called_once()
        assert context.abort.call_args[0][0] == grpc.StatusCode.UNAVAILABLE

    @pytest.mark.asyncio
    async def test_none_key_returns_unavailable(self):
        """When server API key is None, all calls must be rejected."""
        interceptor = InternalAPIKeyInterceptor(None)
        continuation = AsyncMock()
        details = _make_handler_call_details(
            metadata=[("x-internal-api-key", "any-key")]
        )

        handler = await interceptor.intercept_service(continuation, details)

        continuation.assert_not_called()

        context = AsyncMock()
        await handler.unary_unary(None, context)
        context.abort.assert_called_once()
        assert context.abort.call_args[0][0] == grpc.StatusCode.UNAVAILABLE

    @pytest.mark.asyncio
    async def test_metadata_key_is_case_sensitive(self):
        """gRPC metadata keys are lowercase. Wrong case must fail."""
        interceptor = InternalAPIKeyInterceptor("valid-key")
        continuation = AsyncMock()
        # gRPC lowercases metadata keys, but testing defense in depth
        details = _make_handler_call_details(
            metadata=[("X-Internal-API-Key", "valid-key")]
        )

        handler = await interceptor.intercept_service(continuation, details)

        # Should fail because metadata dict lookup is case-sensitive
        # and gRPC normalizes to lowercase, but capital key won't match
        continuation.assert_not_called()

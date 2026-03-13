"""
gRPC server interceptor for internal API key authentication.

Validates the 'x-internal-api-key' metadata header on every incoming
gRPC call. Rejects unauthenticated requests with UNAUTHENTICATED status.
"""

import logging
import grpc

logger = logging.getLogger(__name__)

_SKIP_METHODS = frozenset()  # Add method names here if any should be public


class InternalAPIKeyInterceptor(grpc.aio.ServerInterceptor):
    """Validates x-internal-api-key metadata on every gRPC call."""

    def __init__(self, internal_api_key: str):
        self._key = internal_api_key

    async def intercept_service(self, continuation, handler_call_details):
        # Allow health-check or reflection methods if needed
        method = handler_call_details.method or ""
        if method in _SKIP_METHODS:
            return await continuation(handler_call_details)

        # Fail closed: if no key is configured, reject everything
        if not self._key:
            logger.error(
                "gRPC call to %s rejected: INTERNAL_API_KEY not configured", method
            )
            return _abort_handler(
                grpc.StatusCode.UNAVAILABLE,
                "Internal API key not configured on server",
            )

        # Extract key from invocation metadata
        metadata = dict(handler_call_details.invocation_metadata or [])
        client_key = metadata.get("x-internal-api-key", "")

        if not client_key:
            logger.warning("gRPC call to %s rejected: missing API key", method)
            return _abort_handler(
                grpc.StatusCode.UNAUTHENTICATED,
                "Missing x-internal-api-key metadata",
            )

        if client_key != self._key:
            logger.warning("gRPC call to %s rejected: invalid API key", method)
            return _abort_handler(
                grpc.StatusCode.PERMISSION_DENIED,
                "Invalid internal API key",
            )

        return await continuation(handler_call_details)


def _abort_handler(code, details):
    """Return a generic handler that immediately aborts with the given status."""

    async def _unary_unary(request, context):
        await context.abort(code, details)

    async def _unary_stream(request, context):
        await context.abort(code, details)

    async def _stream_unary(request_iterator, context):
        await context.abort(code, details)

    async def _stream_stream(request_iterator, context):
        await context.abort(code, details)

    return grpc.unary_unary_rpc_method_handler(
        _unary_unary,
    )

from fastapi import status
from fastapi.responses import JSONResponse
import logging
from orchestration.config import settings

logger = logging.getLogger(__name__)

# Paths that bypass the internal key. Each one is health or discovery, or
# authenticates its caller itself (workers carry their own token). Envoy's REST
# xDS client cannot send the key, so discovery stays open; the route table and
# debug dump list every worker's address and need the key.
INTERNAL_AUTH_SKIP_PATHS = [
    "/health",
    "/v1/workers/register",
    "/v1/workers/channel",
    "/v3/discovery:clusters",
    "/v3/discovery:endpoints",
    "/v3/healthz",
]

class InternalAuthMiddleware:
    def __init__(self, app, internal_api_key: str, skip_paths: list = None):
        self.app = app
        self.internal_api_key = internal_api_key
        self.skip_paths = [p.rstrip("/") for p in (skip_paths or [])]

    async def _reject(self, scope, receive, send, status_code: int, detail: str):
        """Refuse a request in the shape its protocol expects."""
        if scope["type"] == "websocket":
            # Sent before any accept, so the handshake itself fails.
            await send({"type": "websocket.close", "code": 1008})
            return
        response = JSONResponse(status_code=status_code, content={"detail": detail})
        await response(scope, receive, send)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" and scope["type"] != "websocket":
            await self.app(scope, receive, send)
            return

        # WebSockets are checked like anything else. A browser cannot set the
        # header, but a browser never reaches this port: the gateway mints its
        # own upstream connection and does send it.
        path = scope.get("path", "")
        norm_path = path.rstrip("/") if path != "/" else path

        # 2. Skip validation for whitelisted paths
        if norm_path in self.skip_paths:
            await self.app(scope, receive, send)
            return

        # 3. Validate internal API key
        headers = dict(scope.get("headers", []))
        # Headers are bytes in ASGI scope
        api_key_bytes = headers.get(b"x-internal-api-key") or headers.get(b"x-internal-key")
        api_key = api_key_bytes.decode("utf-8") if api_key_bytes else None

        if not api_key:
            logger.warning(f"Unauthorized access attempt to {path}: Missing API Key")
            await self._reject(
                scope, receive, send, status.HTTP_401_UNAUTHORIZED,
                "Missing X-Internal-API-Key header",
            )
            return

        if api_key != self.internal_api_key:
            logger.warning(f"Unauthorized access attempt to {path}: Invalid API Key")
            await self._reject(
                scope, receive, send, status.HTTP_403_FORBIDDEN,
                "Invalid internal API key",
            )
            return

        await self.app(scope, receive, send)

# Create the middleware wrapper
def internal_auth_middleware_factory(app):
    return InternalAuthMiddleware(
        app, 
        internal_api_key=settings.internal_api_key, 
        skip_paths=["/health"]
    )

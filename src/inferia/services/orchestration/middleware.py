from fastapi import Request, status
from fastapi.responses import JSONResponse
import logging
from inferia.services.orchestration.config import settings

logger = logging.getLogger(__name__)

class InternalAuthMiddleware:
    def __init__(self, app, internal_api_key: str, skip_paths: list = None):
        self.app = app
        self.internal_api_key = internal_api_key
        self.skip_paths = [p.rstrip("/") for p in (skip_paths or [])]

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" and scope["type"] != "websocket":
            await self.app(scope, receive, send)
            return

        # 1. Skip validation for WebSockets (browsers can't send headers)
        if scope["type"] == "websocket":
            await self.app(scope, receive, send)
            return

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
            response = JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"detail": "Missing X-Internal-API-Key header"},
            )
            await response(scope, receive, send)
            return

        if api_key != self.internal_api_key:
            logger.warning(f"Unauthorized access attempt to {path}: Invalid API Key")
            response = JSONResponse(
                status_code=status.HTTP_403_FORBIDDEN,
                content={"detail": "Invalid internal API key"},
            )
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)

# Create the middleware wrapper
def internal_auth_middleware_factory(app):
    return InternalAuthMiddleware(
        app, 
        internal_api_key=settings.internal_api_key, 
        skip_paths=["/health", "/deployment/ws"]
    )

"""
Middleware for validating internal API key from service-to-service requests.
"""

from fastapi import Request, HTTPException, status
from inferia.services.api_gateway.config import settings


async def internal_api_key_middleware(request: Request, call_next):
    """
    Validate internal API key for /internal/* endpoints.
    These endpoints should only be called by the inference gateway.

    SECURITY: API keys MUST be passed via headers only, never query parameters,
    as query params are logged in web server access logs and browser history.
    """
    # Only check internal API key for /internal/* paths
    if request.url.path.startswith("/internal"):
        # Support both standard header and custom one
        api_key = request.headers.get("X-Internal-API-Key") or request.headers.get(
            "X-Internal-Key"
        )

        # SECURITY: Query parameter fallback has been removed to prevent
        # API key exposure in server logs and browser history.
        # API keys must always be passed via headers.

        if not api_key:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Missing X-Internal-API-Key header",
            )

        if api_key != settings.internal_api_key:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Invalid internal API key"
            )

    response = await call_next(request)
    return response

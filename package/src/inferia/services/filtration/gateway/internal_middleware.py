"""
Middleware for validating internal API key from service-to-service requests.
"""

from fastapi import Request, HTTPException, status
from config import settings


async def internal_api_key_middleware(request: Request, call_next):
    """
    Validate internal API key for /internal/* endpoints.
    These endpoints should only be called by the inference gateway.
    """
    # Only check internal API key for /internal/* paths
    if request.url.path.startswith("/internal"):
        api_key = request.headers.get("X-Internal-API-Key")
        
        if not api_key:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Missing X-Internal-API-Key header"
            )
        
        if api_key != settings.internal_api_key:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Invalid internal API key"
            )
    
    response = await call_next(request)
    return response

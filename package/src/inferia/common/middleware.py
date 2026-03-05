"""
Shared middleware for the InferiaLLM ecosystem.
"""

import logging
import uuid
from typing import List, Optional
from fastapi import Request, HTTPException, status
from inferia.common.http_client import request_id_ctx

logger = logging.getLogger(__name__)

def create_internal_auth_middleware(
    internal_api_key: str, 
    check_path_prefix: Optional[str] = None, 
    skip_paths: Optional[List[str]] = None
):
    """
    Factory method to create a middleware function for internal API key validation.
    
    Args:
        internal_api_key: The secret key to validate against.
        check_path_prefix: If provided, only paths starting with this will be validated.
        skip_paths: List of exact paths to skip validation for.
    """
    async def internal_auth_middleware(request: Request, call_next):
        # 1. Handle Request ID for tracing
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        token = request_id_ctx.set(request_id)
        
        path = request.url.path
        
        try:
            # Normalize path for comparison (remove trailing slash)
            norm_path = path.rstrip("/") if path != "/" else path
            # Normalize skip_paths
            norm_skip_paths = [p.rstrip("/") if p != "/" else p for p in (skip_paths or [])]

            # 2. Skip validation for specific paths (e.g., /health)
            if norm_path in norm_skip_paths:
                logger.info(f"Skipping auth for whitelisted path: {path}")
                response = await call_next(request)
                if hasattr(response, "headers"):
                    response.headers["X-Request-ID"] = request_id
                return response
                
            # WebSocket handshake check - browsers can't set custom headers for WS
            upgrade_header = request.headers.get("upgrade", "").lower()
            if upgrade_header == "websocket":
                logger.info(f"Allowing WebSocket handshake for {path}")
                response = await call_next(request)
                return response

            # 3. Only check if path prefix matches (if provided)
            if check_path_prefix and not path.startswith(check_path_prefix):
                response = await call_next(request)
                if hasattr(response, "headers"):
                    response.headers["X-Request-ID"] = request_id
                return response
            
            # 3. Validate internal API key
            api_key = request.headers.get("X-Internal-API-Key") or request.headers.get(
                "X-Internal-Key"
            )

            if not api_key:
                logger.warning(f"Unauthorized access attempt to {path}: Missing API Key")
                from fastapi.responses import JSONResponse
                return JSONResponse(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    content={"detail": "Missing X-Internal-API-Key header"},
                )

            if api_key != internal_api_key:
                logger.warning(f"Unauthorized access attempt to {path}: Invalid API Key")
                from fastapi.responses import JSONResponse
                return JSONResponse(
                    status_code=status.HTTP_403_FORBIDDEN, 
                    content={"detail": "Invalid internal API key"},
                )

            response = await call_next(request)
            if hasattr(response, "headers"):
                response.headers["X-Request-ID"] = request_id
            return response
        finally:
            request_id_ctx.reset(token)

    return internal_auth_middleware

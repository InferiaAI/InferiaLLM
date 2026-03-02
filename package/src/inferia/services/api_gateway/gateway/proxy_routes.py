"""
Proxy routes for routing requests to downstream services.
Handles dashboard → orchestration service proxying.
"""

from typing import Dict, Optional
import httpx
import logging

from fastapi import APIRouter, Request, Response, HTTPException, Depends
from inferia.services.api_gateway.rbac.middleware import get_current_user_from_request
from inferia.services.api_gateway.models import UserContext
from inferia.services.api_gateway.config import settings
from inferia.services.api_gateway.gateway.http_client import gateway_http_client
from inferia.services.api_gateway.gateway.rate_limiter import rate_limiter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["Proxy API"])

ORCHESTRATION_URL = settings.orchestration_url or "http://localhost:8080"


async def proxy_request(
    method: str,
    path: str,
    request: Request,
    target_url: str,
    user_context: UserContext,
) -> Response:
    """Proxy a request to a downstream service."""

    # Apply rate limiting to all proxy requests
    await rate_limiter.check_rate_limit(request)

    url = f"{target_url}/{path}"

    # Build headers
    headers = dict(request.headers)
    # Remove auth header since we're using internal trust
    headers.pop("Authorization", None)
    # Remove internal API key from being forwarded to prevent exposure in downstream logs
    headers.pop("X-Internal-API-Key", None)

    # Pass internal API key for service-to-service authentication
    headers["X-Internal-API-Key"] = settings.internal_api_key
    
    # Pass user context for authorization at downstream service
    headers["X-User-ID"] = str(user_context.user_id)
    headers["X-Organization-ID"] = str(user_context.org_id)
    # Use X-Gateway-Key header that downstream services should validate
    headers["X-Gateway-Request"] = "true"

    content = await request.body()

    try:
        client = gateway_http_client.get_proxy_client()
        response = await client.request(
            method=method,
            url=url,
            headers=headers,
            content=content,
            params=request.query_params,
        )

        return Response(
            content=response.content,
            status_code=response.status_code,
            headers=dict(response.headers),
        )
    except httpx.RequestError as e:
        logger.error(f"Proxy request failed: {e}")
        raise HTTPException(status_code=503, detail=f"Service unavailable: {e}")


@router.api_route(
    "/deployments/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"]
)
async def proxy_deployments(
    request: Request,
    path: str,
    user_context: UserContext = Depends(get_current_user_from_request),
):
    """Proxy deployment operations to orchestration service."""
    return await proxy_request(
        method=request.method,
        path=f"deployments/{path}",
        request=request,
        target_url=ORCHESTRATION_URL,
        user_context=user_context,
    )


@router.api_route(
    "/pools/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"]
)
async def proxy_pools(
    request: Request,
    path: str,
    user_context: UserContext = Depends(get_current_user_from_request),
):
    """Proxy compute pool operations to orchestration service."""
    return await proxy_request(
        method=request.method,
        path=f"listPools/{path}".replace("listPools/", "")
        if "listPools" in str(request.url)
        else f"pools/{path}",
        request=request,
        target_url=ORCHESTRATION_URL,
        user_context=user_context,
    )


@router.api_route("/logs/{path:path}", methods=["GET"])
async def proxy_logs(
    request: Request,
    path: str,
    user_context: UserContext = Depends(get_current_user_from_request),
):
    """Proxy log streaming from orchestration service."""
    return await proxy_request(
        method="GET",
        path=f"logs/{path}",
        request=request,
        target_url=ORCHESTRATION_URL,
        user_context=user_context,
    )


@router.api_route("/provider/resources", methods=["GET"])
async def proxy_provider_resources(
    request: Request,
    user_context: UserContext = Depends(get_current_user_from_request),
):
    """Proxy provider resources list from orchestration service."""
    return await proxy_request(
        method="GET",
        path="provider/resources",
        request=request,
        target_url=ORCHESTRATION_URL,
        user_context=user_context,
    )


@router.api_route("/inventory/{path:path}", methods=["GET", "POST"])
async def proxy_inventory(
    request: Request,
    path: str,
    user_context: UserContext = Depends(get_current_user_from_request),
):
    """Proxy inventory operations to orchestration service."""
    return await proxy_request(
        method=request.method,
        path=f"inventory/{path}",
        request=request,
        target_url=ORCHESTRATION_URL,
        user_context=user_context,
    )


@router.api_route(
    "/deployment/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"]
)
async def proxy_deployment(
    request: Request,
    path: str,
    user_context: UserContext = Depends(get_current_user_from_request),
):
    """Proxy all deployment operations to orchestration service."""
    return await proxy_request(
        method=request.method,
        path=f"deployment/{path}",
        request=request,
        target_url=ORCHESTRATION_URL,
        user_context=user_context,
    )

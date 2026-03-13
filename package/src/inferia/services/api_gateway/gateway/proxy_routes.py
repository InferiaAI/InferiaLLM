"""
Proxy routes for routing requests to downstream services.
Handles dashboard → orchestration service proxying.
"""

from typing import Dict, Optional
import httpx
import logging
import asyncio
import websockets

from fastapi import (
    APIRouter,
    Request,
    Response,
    HTTPException,
    Depends,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from inferia.services.api_gateway.rbac.middleware import get_current_user_from_request
from inferia.services.api_gateway.models import UserContext, PermissionEnum
from inferia.services.api_gateway.rbac.authorization import authz_service
from inferia.services.api_gateway.config import settings
from inferia.services.api_gateway.gateway.http_client import gateway_http_client
from inferia.services.api_gateway.gateway.rate_limiter import rate_limiter
from inferia.services.api_gateway.db.database import AsyncSessionLocal
from inferia.services.api_gateway.rbac.auth import auth_service
from inferia.services.api_gateway.db.models import Role
from inferia.services.api_gateway.rbac.permissions import normalize_permissions
from sqlalchemy.future import select

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["Proxy API"])

ORCHESTRATION_URL = settings.orchestration_url or "http://localhost:8080"


def _require_proxy_permission(user_context: UserContext, method: str, path: str) -> None:
    normalized_method = method.upper()
    normalized_path = (path or "").strip("/").lower()

    if normalized_method == "POST":
        # Deployment RPC-style endpoints use POST for non-create actions.
        # Map those explicitly to update/delete permissions.
        if normalized_path.startswith("deployment/deletepool"):
            authz_service.require_permission(user_context, PermissionEnum.DEPLOYMENT_DELETE)
            return
        if normalized_path.startswith("deployment/stoppool"):
            authz_service.require_permission(user_context, PermissionEnum.DEPLOYMENT_DELETE)
            return
        if normalized_path.startswith("deployment/deploy") or normalized_path.startswith("deployment/createpool"):
            authz_service.require_permission(user_context, PermissionEnum.DEPLOYMENT_CREATE)
            return
        if normalized_path.startswith("deployment/"):
            authz_service.require_permission(user_context, PermissionEnum.DEPLOYMENT_UPDATE)
            return

    if normalized_method in {"GET", "HEAD"}:
        authz_service.require_permission(user_context, PermissionEnum.DEPLOYMENT_LIST)
    elif normalized_method == "POST":
        authz_service.require_permission(user_context, PermissionEnum.DEPLOYMENT_CREATE)
    elif normalized_method in {"PUT", "PATCH"}:
        authz_service.require_permission(user_context, PermissionEnum.DEPLOYMENT_UPDATE)
    elif normalized_method == "DELETE":
        authz_service.require_permission(user_context, PermissionEnum.DEPLOYMENT_DELETE)
    else:
        authz_service.require_permission(user_context, PermissionEnum.DEPLOYMENT_LIST)


async def _get_ws_user_context(token: str) -> UserContext:
    async with AsyncSessionLocal() as db:
        user, org_id, roles = await auth_service.get_current_user(db, token)

        permissions_set = set()
        if roles:
            stmt = select(Role).where(Role.name.in_(roles))
            result = await db.execute(stmt)
            role_records = result.scalars().all()
            for role_record in role_records:
                if role_record.permissions:
                    permissions_set.update(role_record.permissions)

        permissions, _, _ = normalize_permissions(permissions_set)

        return UserContext(
            user_id=user.id,
            username=user.email,
            email=user.email,
            roles=roles,
            permissions=permissions,
            org_id=org_id,
            quota_limit=10000,
            quota_used=0,
        )


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


@router.websocket("/deployment/ws")
async def proxy_deployment_ws(websocket: WebSocket):
    token = websocket.query_params.get("access_token") or websocket.query_params.get(
        "token"
    )
    if not token:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    try:
        user_context = await _get_ws_user_context(token)
        authz_service.require_permission(user_context, PermissionEnum.DEPLOYMENT_LIST)
    except Exception as e:
        logger.warning(f"Rejected deployment WS connection: {e}")
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await websocket.accept()

    upstream_base = ORCHESTRATION_URL.replace("http://", "ws://").replace(
        "https://", "wss://"
    )
    upstream_ws_url = f"{upstream_base.rstrip('/')}/deployment/ws"
    upstream_headers = {
        "X-Internal-API-Key": settings.internal_api_key,
        "X-Gateway-Request": "true",
        "X-User-ID": str(user_context.user_id),
        "X-Organization-ID": str(user_context.org_id or ""),
    }

    try:
        async with websockets.connect(
            upstream_ws_url,
            additional_headers=upstream_headers,
        ) as upstream:

            async def client_to_upstream():
                while True:
                    payload = await websocket.receive()
                    event_type = payload.get("type")
                    if event_type == "websocket.disconnect":
                        break
                    if payload.get("text") is not None:
                        await upstream.send(payload["text"])
                    elif payload.get("bytes") is not None:
                        await upstream.send(payload["bytes"])

            async def upstream_to_client():
                async for message in upstream:
                    if isinstance(message, bytes):
                        await websocket.send_bytes(message)
                    else:
                        await websocket.send_text(message)

            tasks = {
                asyncio.create_task(client_to_upstream()),
                asyncio.create_task(upstream_to_client()),
            }
            done, pending = await asyncio.wait(
                tasks, return_when=asyncio.FIRST_COMPLETED
            )
            for task in pending:
                task.cancel()
            for task in done:
                exc = task.exception()
                if exc:
                    raise exc
    except WebSocketDisconnect:
        logger.info("Client WebSocket disconnected")
    except Exception as e:
        logger.error(f"Deployment WS proxy error: {e}")
    finally:
        try:
            await websocket.close()
        except Exception:
            pass


@router.api_route(
    "/deployments/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"]
)
async def proxy_deployments(
    request: Request,
    path: str,
    user_context: UserContext = Depends(get_current_user_from_request),
):
    """Proxy deployment operations to orchestration service."""
    _require_proxy_permission(user_context, request.method, f"deployments/{path}")
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
    _require_proxy_permission(user_context, request.method, f"pools/{path}")
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
    _require_proxy_permission(user_context, request.method, f"logs/{path}")
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
    _require_proxy_permission(user_context, request.method, "provider/resources")
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
    _require_proxy_permission(user_context, request.method, f"inventory/{path}")
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
    _require_proxy_permission(user_context, request.method, f"deployment/{path}")
    return await proxy_request(
        method=request.method,
        path=f"deployment/{path}",
        request=request,
        target_url=ORCHESTRATION_URL,
        user_context=user_context,
    )

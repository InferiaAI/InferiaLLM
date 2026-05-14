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

# Separate router (no prefix) for inferia-worker passthrough endpoints. The
# worker hits the api_gateway at `/v1/workers/...` exactly, so we cannot
# share the `/api/v1` prefix here. Mounted on the FastAPI app at root.
worker_passthrough_router = APIRouter(tags=["Worker Passthrough"])

ORCHESTRATION_URL = settings.orchestration_url or "http://localhost:8080"


def _require_proxy_permission(
    user_context: UserContext, method: str, path: str
) -> None:
    normalized_method = method.upper()
    normalized_path = (path or "").strip("/").lower()

    if normalized_method == "POST":
        # Deployment RPC-style endpoints use POST for non-create actions.
        # Map those explicitly to update/delete permissions. The rich
        # "Add Node" page in the UI keeps using the original /createpool
        # / /stoppool / /deletepool surface; the node-centric refactor
        # added /v1/nodes/* alongside without retiring the originals.
        if normalized_path.startswith("deployment/deletepool"):
            authz_service.require_permission(
                user_context, PermissionEnum.DEPLOYMENT_DELETE
            )
            return
        if normalized_path.startswith("deployment/stoppool"):
            authz_service.require_permission(
                user_context, PermissionEnum.DEPLOYMENT_DELETE
            )
            return
        if normalized_path.startswith(
            "deployment/deploy"
        ) or normalized_path.startswith("deployment/createpool"):
            authz_service.require_permission(
                user_context, PermissionEnum.DEPLOYMENT_CREATE
            )
            return
        if normalized_path.startswith("deployment/"):
            authz_service.require_permission(
                user_context, PermissionEnum.DEPLOYMENT_UPDATE
            )
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
        logger.warning("WebSocket connection rejected: no token provided")
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    try:
        user_context = await _get_ws_user_context(token)
        authz_service.require_permission(user_context, PermissionEnum.DEPLOYMENT_LIST)
    except Exception as e:
        logger.warning(f"Rejected deployment WS connection: {e}", exc_info=True)
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
    "/nodes/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
)
async def proxy_nodes(
    request: Request,
    path: str,
    user_context: UserContext = Depends(get_current_user_from_request),
):
    """Proxy node-centric operations to the orchestration service.

    Method-aware RBAC:
      GET             → DEPLOYMENT_LIST
      POST / PATCH    → DEPLOYMENT_CREATE / DEPLOYMENT_UPDATE
      DELETE          → DEPLOYMENT_DELETE
    """
    m = request.method.upper()
    if m == "GET":
        authz_service.require_permission(user_context, PermissionEnum.DEPLOYMENT_LIST)
    elif m == "POST":
        authz_service.require_permission(user_context, PermissionEnum.DEPLOYMENT_CREATE)
    elif m == "PATCH" or m == "PUT":
        authz_service.require_permission(user_context, PermissionEnum.DEPLOYMENT_UPDATE)
    elif m == "DELETE":
        authz_service.require_permission(user_context, PermissionEnum.DEPLOYMENT_DELETE)
    return await proxy_request(
        method=request.method,
        path=f"v1/nodes/{path}",
        request=request,
        target_url=ORCHESTRATION_URL,
        user_context=user_context,
    )


# /pools/* proxy removed in the node-centric refactor (2026-05-14).


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


async def _proxy_unauthenticated(
    method: str, path: str, request: Request
) -> Response:
    """HTTP proxy that does NOT inject user-context headers.

    Used for the worker-facing /v1/workers/register endpoint where the
    caller carries its own bootstrap JWT (verified by orchestration), not
    a user JWT.
    """
    url = f"{ORCHESTRATION_URL}/{path}"
    headers = dict(request.headers)
    headers.pop("X-Internal-API-Key", None)
    # Still set the internal API key so orchestration's InternalAuthMiddleware
    # accepts the request — the bootstrap JWT is the additional auth layer
    # that orchestration's /v1/workers/register handler enforces.
    headers["X-Internal-API-Key"] = settings.internal_api_key
    headers["X-Gateway-Request"] = "true"
    content = await request.body()
    try:
        client = gateway_http_client.get_proxy_client()
        response = await client.request(
            method=method, url=url, headers=headers,
            content=content, params=request.query_params,
        )
        return Response(
            content=response.content,
            status_code=response.status_code,
            headers=dict(response.headers),
        )
    except httpx.RequestError as e:
        logger.error(f"Worker proxy request failed: {e}")
        raise HTTPException(status_code=503, detail=f"Service unavailable: {e}")


@worker_passthrough_router.api_route("/v1/workers/{path:path}", methods=["GET", "POST"])
async def proxy_worker_endpoints(request: Request, path: str):
    """Proxy worker control-plane HTTP endpoints to the orchestration service.
    Workers authenticate with their own bootstrap-JWT / worker-JWT — the
    api_gateway user-auth middleware is skipped upstream of this route."""
    return await _proxy_unauthenticated(
        method=request.method, path=f"v1/workers/{path}", request=request,
    )


@worker_passthrough_router.websocket("/v1/workers/channel")
async def proxy_worker_channel(websocket: WebSocket):
    """Proxy the worker WS control channel to orchestration. The worker
    presents its own ``Authorization: Bearer <worker_jwt>`` header which
    orchestration verifies; we forward it unchanged."""
    auth = websocket.headers.get("authorization", "")
    if not auth:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await websocket.accept()

    upstream_base = ORCHESTRATION_URL.replace("http://", "ws://").replace(
        "https://", "wss://"
    )
    upstream_ws_url = f"{upstream_base.rstrip('/')}/v1/workers/channel"
    upstream_headers = {
        "Authorization": auth,
        "X-Internal-API-Key": settings.internal_api_key,
        "X-Gateway-Request": "true",
    }
    try:
        async with websockets.connect(
            upstream_ws_url,
            additional_headers=upstream_headers,
        ) as upstream:
            async def client_to_upstream():
                while True:
                    payload = await websocket.receive()
                    if payload.get("type") == "websocket.disconnect":
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
    except WebSocketDisconnect:
        logger.info("Worker WS disconnected")
    except Exception as e:
        logger.warning(f"Worker WS proxy error: {e}")
    finally:
        try:
            await websocket.close()
        except Exception:
            pass


@router.api_route(
    "/admin/workers/{path:path}",
    methods=["GET", "POST", "DELETE"],
)
async def proxy_admin_workers(
    request: Request,
    path: str,
    user_context: UserContext = Depends(get_current_user_from_request),
):
    """Proxy inferia-worker admin operations to the orchestration service.

    The orchestration side's ``/v1/admin/workers/...`` router treats the
    request as authorised (it trusts the api_gateway proxy boundary). RBAC
    is enforced here:

    * ``GET``    → ``DEPLOYMENT_LIST``   — view workers in a pool
    * ``POST``   → ``DEPLOYMENT_CREATE`` — mint a bootstrap token
    * ``DELETE`` → ``DEPLOYMENT_DELETE`` — revoke a worker
    """
    method = request.method.upper()
    if method == "GET":
        authz_service.require_permission(user_context, PermissionEnum.DEPLOYMENT_LIST)
    elif method == "POST":
        authz_service.require_permission(user_context, PermissionEnum.DEPLOYMENT_CREATE)
    elif method == "DELETE":
        authz_service.require_permission(user_context, PermissionEnum.DEPLOYMENT_DELETE)
    return await proxy_request(
        method=request.method,
        path=f"v1/admin/workers/{path}",
        request=request,
        target_url=ORCHESTRATION_URL,
        user_context=user_context,
    )

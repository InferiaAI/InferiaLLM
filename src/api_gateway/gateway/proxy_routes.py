"""
Proxy routes for routing requests to downstream services.
Handles dashboard → orchestration service proxying.
"""

from typing import Dict, Optional
import httpx
import logging
import asyncio
import posixpath
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
from fastapi.responses import StreamingResponse
from api_gateway.rbac.middleware import (
    get_current_user_from_request,
    resolve_token_to_user_context,
)
from api_gateway.models import UserContext, PermissionEnum
from api_gateway.rbac.authorization import authz_service
from api_gateway.config import settings
from api_gateway.gateway.http_client import gateway_http_client
from api_gateway.gateway.rate_limiter import rate_limiter
from api_gateway.db.database import AsyncSessionLocal

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1", tags=["Proxy API"])

# Separate router (no prefix) for inferia-worker passthrough endpoints. The
# worker hits the api_gateway at `/v1/workers/...` exactly, so we cannot
# share the `/v1` prefix here. Mounted on the FastAPI app at root.
worker_passthrough_router = APIRouter(tags=["Worker Passthrough"])

# Dedicated router for the OCI registry mirror (/v2/*). The OCI spec
# hard-codes <host>/v2, so this must live at the ROOT of the unified port,
# NOT under /api. A later task registers this on the parent app at root;
# it is intentionally NOT included in the gateway app.include_router calls.
ollama_registry_router = APIRouter(tags=["Ollama OCI Mirror"])

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
    """Authenticate a WebSocket token using the active auth provider.

    Delegates to resolve_token_to_user_context which mirrors the HTTP
    middleware branching (local-only, inferiaauth, or oidc).  Raises
    HTTPException(401) on failure; callers should close the WS with 1008.
    """
    async with AsyncSessionLocal() as db:
        return await resolve_token_to_user_context(db, token)


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


@router.api_route("/providers/{path:path}", methods=["GET"])
async def proxy_providers(
    request: Request,
    path: str,
    user_context: UserContext = Depends(get_current_user_from_request),
):
    """Proxy provider catalog endpoints to the orchestration service.

    Mounted under /api/v1; orchestration exposes the catalog at the
    literal path /api/v1/providers/aws/instance-catalog (see T22), so
    we forward verbatim under that prefix. RBAC: any authenticated user
    can read the catalog — it's static reference data the wizard needs."""
    return await proxy_request(
        method=request.method,
        path=f"api/v1/providers/{path}",
        request=request,
        target_url=ORCHESTRATION_URL,
        user_context=user_context,
    )


def _require_models_permission(user_context: UserContext, method: str) -> None:
    """Method-aware RBAC for the model-cache management endpoints.

      GET    → MODEL_LIST   — list cached models / progress
      POST   → MODEL_ADD    — enqueue a model download
      DELETE → MODEL_DELETE — remove a cached model
    """
    m = method.upper()
    if m == "GET":
        authz_service.require_permission(user_context, PermissionEnum.MODEL_LIST)
    elif m == "POST":
        authz_service.require_permission(user_context, PermissionEnum.MODEL_ADD)
    elif m == "DELETE":
        authz_service.require_permission(user_context, PermissionEnum.MODEL_DELETE)
    else:
        raise HTTPException(status_code=405, detail="method not allowed")


# Bare collection (list + add). Registered WITHOUT a trailing slash so the
# dashboard's `GET/POST /api/v1/models` matches directly instead of triggering
# a 307 redirect (the `{path:path}` route below only matches `/models/...`),
# which broke the add request. Forwards to orchestration's `/v1/models`.
@router.api_route("/models", methods=["GET", "POST"])
async def proxy_models_collection(
    request: Request,
    user_context: UserContext = Depends(get_current_user_from_request),
):
    """Proxy the model-cache collection (list/add) to the orchestration service."""
    _require_models_permission(user_context, request.method)
    return await proxy_request(
        method=request.method,
        path="v1/models",
        request=request,
        target_url=ORCHESTRATION_URL,
        user_context=user_context,
    )


@router.api_route("/models/{path:path}", methods=["GET", "POST", "DELETE"])
async def proxy_models(
    request: Request,
    path: str,
    user_context: UserContext = Depends(get_current_user_from_request),
):
    """Proxy model-cache item endpoints ({id}/progress, {id} delete) to
    the orchestration service."""
    _require_models_permission(user_context, request.method)
    return await proxy_request(
        method=request.method,
        path=f"v1/models/{path}",
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


@router.websocket("/admin/workers/{node_id}/logs")
async def proxy_admin_workers_logs(websocket: WebSocket, node_id: str):
    """Proxy live worker-container log streams to the dashboard.

    The dashboard supplies ?access_token=<jwt> on the WS URL because
    browsers can't attach Authorization headers to WebSocket upgrades.
    We validate the JWT + DEPLOYMENT_LIST permission, then relay frames
    to orchestration's /v1/admin/workers/{node_id}/logs.
    """
    await _proxy_admin_workers_ws(websocket, node_id, subpath="logs",
                                   permission=PermissionEnum.DEPLOYMENT_LIST)


@router.websocket("/admin/workers/{node_id}/shell")
async def proxy_admin_workers_shell(websocket: WebSocket, node_id: str):
    """Proxy interactive shell WS to the dashboard.

    Same auth pattern as /logs but requires DEPLOYMENT_UPDATE — a shell
    can mutate the running container, so we gate it more tightly.
    """
    await _proxy_admin_workers_ws(websocket, node_id, subpath="shell",
                                   permission=PermissionEnum.DEPLOYMENT_UPDATE)


async def _proxy_admin_workers_ws(websocket: WebSocket, node_id: str, *, subpath: str, permission):
    token = websocket.query_params.get("access_token") or websocket.query_params.get("token")
    if not token:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    try:
        user_context = await _get_ws_user_context(token)
        authz_service.require_permission(user_context, permission)
    except Exception as e:
        logger.warning(f"Rejected admin workers WS: {e}")
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await websocket.accept()

    # Carry the upstream query string forward (deployment=… / container=…)
    # but drop the access_token — orchestration uses the internal API key,
    # and we don't want the user JWT travelling further than necessary.
    upstream_qs = "&".join(
        f"{k}={v}" for k, v in websocket.query_params.items()
        if k not in ("access_token", "token")
    )
    upstream_qs = f"?{upstream_qs}" if upstream_qs else ""

    upstream_base = ORCHESTRATION_URL.replace("http://", "ws://").replace("https://", "wss://")
    upstream_url = f"{upstream_base.rstrip('/')}/v1/admin/workers/{node_id}/{subpath}{upstream_qs}"
    upstream_headers = {
        "X-Internal-API-Key": settings.internal_api_key,
        "X-Gateway-Request": "true",
        "X-User-ID": str(user_context.user_id),
        "X-Organization-ID": str(user_context.org_id or ""),
    }

    try:
        async with websockets.connect(upstream_url, additional_headers=upstream_headers) as upstream:
            async def c2u():
                while True:
                    payload = await websocket.receive()
                    et = payload.get("type")
                    if et == "websocket.disconnect":
                        break
                    if payload.get("text") is not None:
                        await upstream.send(payload["text"])
                    elif payload.get("bytes") is not None:
                        await upstream.send(payload["bytes"])

            async def u2c():
                async for message in upstream:
                    if isinstance(message, bytes):
                        await websocket.send_bytes(message)
                    else:
                        await websocket.send_text(message)

            tasks = {asyncio.create_task(c2u()), asyncio.create_task(u2c())}
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for t in pending:
                t.cancel()
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.error(f"admin workers WS proxy error: {e}")
    finally:
        try:
            await websocket.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Streaming model-artifact passthroughs (UNAUTHENTICATED by design)
# ---------------------------------------------------------------------------
# Engine containers (ollama, vLLM, etc.) and worker processes pull model
# files directly from the orchestration service's HF pull-through mirror and
# OCI/v2 registry endpoint.  These callers have no dashboard JWT, so these
# routes intentionally skip user authentication.
#
# Large files (multi-GB model weights) MUST be streamed; buffering the body
# in memory would exhaust gateway RAM and break range-request resumption.
# proxy_request() is not used here because it (a) buffers response.content
# and (b) requires a UserContext.  Instead we open an httpx streaming context
# and return a StreamingResponse that pipes bytes directly to the client.

_STREAM_FORWARD_HEADERS = frozenset(
    {
        "content-type", "content-length", "content-range", "accept-ranges",
        "etag", "last-modified",
        # HuggingFace metadata huggingface_hub REQUIRES on the resolve HEAD.
        # `X-Repo-Commit` is the commit hash: if it's absent huggingface_hub
        # raises FileMetadataError("Distant resource does not seem to be on
        # huggingface.co ...") and the vLLM/TEI container crashes before
        # downloading any weights — so the whole HF cache-first path dies.
        # `X-Linked-Etag`/`X-Linked-Size` carry the per-file etag/size for LFS
        # pointer files. Forward them all verbatim from the orchestration mirror.
        "x-repo-commit", "x-linked-etag", "x-linked-size",
        # `Location` is required for the Ollama /v2 blob redirect: ollama's
        # downloader calls resp.Location() on the blob GET and aborts without it.
        "location",
    }
)


async def _streaming_passthrough(method: str, upstream_url: str, request: Request) -> Response:
    """Stream an upstream response byte-for-byte to the caller.

    Forwards ``Range`` and ``Accept-Encoding`` request headers so that engine
    clients can use HTTP range requests to resume interrupted downloads.
    """
    # Forward range / accept-encoding headers from the incoming request.
    forward = {}
    for h in ("range", "accept-encoding", "if-none-match", "if-modified-since"):
        v = request.headers.get(h)
        if v:
            forward[h] = v
    # Attach internal API key so orchestration's InternalAuthMiddleware accepts
    # the request (the /hf and /v2 paths are not in the skip_paths list).
    forward["X-Internal-API-Key"] = settings.internal_api_key
    forward["X-Gateway-Request"] = "true"

    try:
        client = gateway_http_client.get_proxy_client()
        upstream_ctx = client.stream(
            method,
            upstream_url,
            headers=forward,
            params=request.query_params,
        )
        up = await upstream_ctx.__aenter__()

        async def _gen():
            try:
                async for chunk in up.aiter_bytes():
                    yield chunk
            finally:
                await upstream_ctx.__aexit__(None, None, None)

        # Propagate key headers to the caller.
        resp_headers = {
            k: v
            for k, v in up.headers.items()
            if k.lower() in _STREAM_FORWARD_HEADERS
        }
        return StreamingResponse(
            _gen(),
            status_code=up.status_code,
            headers=resp_headers,
            media_type=up.headers.get("content-type", "application/octet-stream"),
        )
    except httpx.RequestError as e:
        logger.error("Streaming passthrough failed: %s", e)
        raise HTTPException(status_code=503, detail=f"Service unavailable: {e}")


@worker_passthrough_router.api_route("/hf/{path:path}", methods=["GET", "HEAD"])
async def proxy_hf_mirror(request: Request, path: str):
    """Stream HuggingFace model artifacts from the orchestration HF mirror.

    Unauthenticated by design — engine containers have no dashboard JWT.
    Large files are streamed, not buffered, to support range requests.

    Path is normalized and confined to the /hf/* prefix to prevent path-
    traversal attacks (e.g. ``%2e%2e`` → ``..``) that would forward the
    internal API key to other orchestration routes.
    """
    normalized = posixpath.normpath(f"/hf/{path}")
    if not (normalized == "/hf" or normalized.startswith("/hf/")):
        raise HTTPException(status_code=400, detail="invalid path")
    upstream_url = f"{ORCHESTRATION_URL}{normalized}"
    return await _streaming_passthrough(request.method, upstream_url, request)


@ollama_registry_router.api_route("/v2/{path:path}", methods=["GET", "HEAD"])
async def proxy_v2_registry(request: Request, path: str):
    """Stream OCI/v2 registry responses from the orchestration service.

    Unauthenticated by design — engine containers have no dashboard JWT.
    Large blobs are streamed, not buffered, to support range requests.

    Path is normalized and confined to the /v2/* prefix to prevent path-
    traversal attacks that would forward the internal API key to other
    orchestration routes.
    """
    normalized = posixpath.normpath(f"/v2/{path}")
    if not (normalized == "/v2" or normalized.startswith("/v2/")):
        raise HTTPException(status_code=400, detail="invalid path")
    upstream_url = f"{ORCHESTRATION_URL}{normalized}"
    return await _streaming_passthrough(request.method, upstream_url, request)


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


@router.api_route("/admin/aws/engine-ami", methods=["GET", "POST"])
@router.api_route("/admin/aws/engine-ami/{path:path}", methods=["GET", "POST"])
async def proxy_admin_engine_ami(
    request: Request,
    path: str = "",
    user_context: UserContext = Depends(get_current_user_from_request),
):
    """Proxy engine-cache AMI bake/list admin operations to orchestration.

    The orchestration ``/v1/admin/aws/engine-ami`` router trusts the gateway
    boundary; RBAC is enforced here:

    * ``GET``  → ``DEPLOYMENT_LIST``   — list baked AMIs / poll bake status
    * ``POST`` → ``DEPLOYMENT_CREATE`` — trigger a bake
    """
    method = request.method.upper()
    if method == "POST":
        authz_service.require_permission(user_context, PermissionEnum.DEPLOYMENT_CREATE)
    else:  # GET
        authz_service.require_permission(user_context, PermissionEnum.DEPLOYMENT_LIST)
    upstream_path = "v1/admin/aws/engine-ami" + (f"/{path}" if path else "")
    return await proxy_request(
        method=request.method,
        path=upstream_path,
        request=request,
        target_url=ORCHESTRATION_URL,
        user_context=user_context,
    )


@router.api_route("/admin/aws/regions", methods=["GET"])
@router.api_route("/admin/aws/instance-types", methods=["GET"])
async def proxy_admin_aws_discovery(
    request: Request,
    user_context: UserContext = Depends(get_current_user_from_request),
):
    """Proxy live AWS discovery (regions + instance types) to orchestration.
    GET only → DEPLOYMENT_LIST (any deployer populates the pool form).

    The gateway compute router prefix is /v1 (mounted under /api on the unified
    app, which strips /api before this handler runs), so request.url.path is
    already /v1/admin/aws/... — no prefix stripping needed.
    """
    authz_service.require_permission(user_context, PermissionEnum.DEPLOYMENT_LIST)
    # Gateway compute router prefix is /v1 (mounted under /api on the unified app,
    # which strips /api before this handler runs), so the path is already /v1/...
    upstream_path = request.url.path.lstrip("/")  # v1/admin/aws/...
    return await proxy_request(
        method=request.method,
        path=upstream_path,
        request=request,
        target_url=ORCHESTRATION_URL,
        user_context=user_context,
    )

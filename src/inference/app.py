"""
Inference Service - Client-Facing API
Proxies requests to the Filtration service for security and policy enforcement,
then routes to the actual model provider.
"""

import logging
import json
from typing import Optional
from jose import JWTError, jwt

from common.jwks_verifier import JWKSVerifier, JWKSVerifyError
from common.schemas.common import HealthCheckResponse
from inference.client import api_gateway_client
from inference.config import settings
from inference.core.http_client import http_client
from inference.core.orchestrator import OrchestrationService
from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Query, Request

from common.exception_handlers import register_exception_handlers
from common.logger import setup_logging
from common.app_setup import setup_cors, add_standard_health_routes

# Configure logging
logger = setup_logging(
    level=settings.log_level,
    service_name="inference-gateway",
    use_json=not settings.is_development,
    logger_name="services.inference",
)

app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="Inference Gateway - OpenAI Compatible Endpoint",
)

# Register standard exception handlers
register_exception_handlers(app)

# CORS configuration (Standardized)
import os

setup_cors(app, os.getenv("ALLOWED_ORIGINS", ""), settings.is_development)


# Add standard / and /health routes
add_standard_health_routes(
    app=app,
    app_name=settings.app_name,
    app_version=settings.app_version,
    environment=settings.environment,
)


@app.on_event("shutdown")
async def shutdown_event():
    await http_client.close_client()
    await api_gateway_client.close_client()


_sandbox_verifier: Optional[JWKSVerifier] = None


def _get_sandbox_verifier() -> JWKSVerifier:
    """Lazily build the JWKS verifier for external-mode sandbox tokens.

    Mirrors api_gateway.rbac.middleware._get_verifier: generic OIDC IdPs issue
    tokens with aud=client_id, while InferiaAuth issues aud=app_namespace.
    """
    global _sandbox_verifier
    if _sandbox_verifier is None:
        audience = (
            settings.oauth_client_id
            if settings.auth_provider == "oidc"
            else settings.app_namespace
        )
        _sandbox_verifier = JWKSVerifier(
            jwks_url=settings.external_auth_url.rstrip("/") + "/.well-known/jwks.json",
            issuer=settings.external_auth_issuer,
            audience=audience,
            cache_ttl=settings.oauth_jwks_cache_ttl_seconds,
            verify=settings.httpx_verify,
        )
    return _sandbox_verifier


def _sandbox_key_from_claims(claims: dict) -> str:
    """Build the `sandbox:{org_id}:{user_id}` key from verified JWT claims.

    The `sub` of an InferiaAuth token is prefixed (`user:<uuid>`); the prefix
    MUST be stripped because the policy engine splits this key on ':' and
    requires exactly 3 parts. org_id comes from the explicit `org_id` claim,
    else the first entry of `org_ids[]` (matches api_gateway extraction).
    """
    sub = claims.get("sub", "") or ""
    user_id = sub.split(":", 1)[1] if ":" in sub else sub
    org_id = claims.get("org_id")
    if not org_id:
        org_ids = claims.get("org_ids") or []
        org_id = org_ids[0] if org_ids else None
    return f"sandbox:{org_id}:{user_id}"


def extract_api_key(authorization: str, sandbox: bool = False) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid API Key format")
    token = authorization.split(" ")[1]

    if sandbox:
        # External SSO (oidc/inferiaauth): the bearer is an EdDSA JWT issued by
        # the IdP, verified via JWKS — NOT the local HS256 secret. The verifier
        # already enforces type==access + iss/aud/sub/exp/iat.
        if settings.is_external_mode:
            try:
                claims = _get_sandbox_verifier().verify_sync(token)
            except JWKSVerifyError:
                raise HTTPException(
                    status_code=401, detail="Invalid JWT token for sandbox mode"
                )
            return _sandbox_key_from_claims(claims)

        # Local mode: built-in HS256 access token.
        try:
            payload = jwt.decode(
                token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm]
            )
            if payload.get("type") != "access":
                raise HTTPException(
                    status_code=401, detail="Invalid token type for sandbox mode"
                )
            return f"sandbox:{payload.get('org_id')}:{payload.get('sub')}"
        except JWTError:
            raise HTTPException(
                status_code=401, detail="Invalid JWT token for sandbox mode"
            )

    return token


def extract_client_ip(request: Request) -> Optional[str]:
    """
    Prefer explicitly provided client IP headers when requests pass through
    upstream proxies. Fall back to connection source IP.
    """
    header_candidates = [
        request.headers.get("X-IP-Address"),
        request.headers.get("X-Client-IP"),
        request.headers.get("X-Forwarded-For"),
        request.headers.get("X-Real-IP"),
    ]

    for raw_ip in header_candidates:
        if not raw_ip:
            continue
        first_ip = raw_ip.split(",")[0].strip()
        if first_ip:
            return first_ip

    if request.client and request.client.host:
        return request.client.host

    return None


async def parse_json_body(request: Request) -> dict:
    try:
        body = await request.json()
    except json.JSONDecodeError as e:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid JSON in request body: {str(e)}",
        )
    return body


# stream_with_tracking removed - logic moved to core.orchestrator.OrchestrationService


@app.get("/v1/models")
async def list_models(
    authorization: str = Header(None),
    sandbox: str = Header(None, alias="x-sandbox"),
):
    """
    List available models.
    """
    is_sandbox = sandbox.lower() == "true" if sandbox else False
    api_key = extract_api_key(authorization, is_sandbox)
    return await OrchestrationService.list_models(api_key, sandbox=is_sandbox)


@app.post("/v1/chat/completions")
async def create_completion(
    request: Request,
    background_tasks: BackgroundTasks,
    authorization: str = Header(None),
    sandbox: str = Header(None, alias="x-sandbox"),
):
    """
    Main chat completion endpoint.
    Delegates orchestration to OrchestrationService.
    """
    is_sandbox = sandbox.lower() == "true" if sandbox else False
    api_key = extract_api_key(authorization, is_sandbox)
    body = await parse_json_body(request)
    client_ip = extract_client_ip(request)

    return await OrchestrationService.handle_completion(
        api_key=api_key,
        body=body,
        background_tasks=background_tasks,
        ip_address=client_ip,
        sandbox=is_sandbox,
    )


@app.post("/v1/embeddings")
async def create_embeddings(
    request: Request,
    background_tasks: BackgroundTasks,
    authorization: str = Header(None),
    sandbox: str = Header(None, alias="x-sandbox"),
):
    """
    Embeddings endpoint - OpenAI compatible.
    Supports text embedding models deployed via Infinity or TEI.
    """
    is_sandbox = sandbox.lower() == "true" if sandbox else False
    api_key = extract_api_key(authorization, is_sandbox)
    body = await parse_json_body(request)
    client_ip = extract_client_ip(request)

    return await OrchestrationService.handle_embeddings(
        api_key=api_key,
        body=body,
        background_tasks=background_tasks,
        ip_address=client_ip,
        sandbox=is_sandbox,
    )


@app.post("/v1/images/generations")
async def create_image(
    request: Request,
    background_tasks: BackgroundTasks,
    authorization: str = Header(None),
    sandbox: str = Header(None, alias="x-sandbox"),
):
    """
    Image generation endpoint - OpenAI compatible (text-to-image).
    Supports image generation models deployed via InferaDiffusion.
    """
    is_sandbox = sandbox.lower() == "true" if sandbox else False
    api_key = extract_api_key(authorization, is_sandbox)
    body = await parse_json_body(request)
    client_ip = extract_client_ip(request)

    return await OrchestrationService.handle_image_generation(
        api_key=api_key,
        body=body,
        background_tasks=background_tasks,
        ip_address=client_ip,
        sandbox=is_sandbox,
    )


@app.post("/v1/images/edits")
async def create_image_edit(
    request: Request,
    background_tasks: BackgroundTasks,
    authorization: str = Header(None),
    sandbox: str = Header(None, alias="x-sandbox"),
):
    """
    Image edit endpoint - OpenAI compatible (image-to-image).
    Supports image editing/variation models deployed via InferaDiffusion.
    """
    is_sandbox = sandbox.lower() == "true" if sandbox else False
    api_key = extract_api_key(authorization, is_sandbox)
    body = await parse_json_body(request)
    client_ip = extract_client_ip(request)

    return await OrchestrationService.handle_image_edit(
        api_key=api_key,
        body=body,
        background_tasks=background_tasks,
        ip_address=client_ip,
        sandbox=is_sandbox,
    )


@app.post("/v1/images/variations")
async def create_image_variation(
    request: Request,
    background_tasks: BackgroundTasks,
    authorization: str = Header(None),
    sandbox: str = Header(None, alias="x-sandbox"),
):
    """
    Image variations endpoint - OpenAI compatible.
    Creates variations of the input image.
    """
    is_sandbox = sandbox.lower() == "true" if sandbox else False
    api_key = extract_api_key(authorization, is_sandbox)
    body = await parse_json_body(request)
    client_ip = extract_client_ip(request)

    return await OrchestrationService.handle_image_variations(
        api_key=api_key,
        body=body,
        background_tasks=background_tasks,
        ip_address=client_ip,
        sandbox=is_sandbox,
    )


@app.post("/v1/videos/generations")
async def create_video(
    request: Request,
    background_tasks: BackgroundTasks,
    authorization: str = Header(None),
    sandbox: str = Header(None, alias="x-sandbox"),
):
    """
    Video generation endpoint - OpenAI compatible (text-to-video and image-to-video).
    Supports video generation models deployed via InferaDiffusion.
    """
    is_sandbox = sandbox.lower() == "true" if sandbox else False
    api_key = extract_api_key(authorization, is_sandbox)
    body = await parse_json_body(request)
    client_ip = extract_client_ip(request)

    return await OrchestrationService.handle_video_generation(
        api_key=api_key,
        body=body,
        background_tasks=background_tasks,
        ip_address=client_ip,
        sandbox=is_sandbox,
    )


@app.post("/v1/videos/edits")
async def create_video_edit(
    request: Request,
    background_tasks: BackgroundTasks,
    authorization: str = Header(None),
    sandbox: str = Header(None, alias="x-sandbox"),
):
    """
    Video edit endpoint - OpenAI compatible.
    Supports video editing models deployed via InferaDiffusion.
    """
    is_sandbox = sandbox.lower() == "true" if sandbox else False
    api_key = extract_api_key(authorization, is_sandbox)
    body = await parse_json_body(request)
    client_ip = extract_client_ip(request)

    return await OrchestrationService.handle_video_edit(
        api_key=api_key,
        body=body,
        background_tasks=background_tasks,
        ip_address=client_ip,
        sandbox=is_sandbox,
    )


@app.post("/v1/videos/extensions")
async def create_video_extension(
    request: Request,
    background_tasks: BackgroundTasks,
    authorization: str = Header(None),
    sandbox: str = Header(None, alias="x-sandbox"),
):
    """
    Video extension endpoint - OpenAI compatible.
    Supports video extension models deployed via InferaDiffusion.
    """
    is_sandbox = sandbox.lower() == "true" if sandbox else False
    api_key = extract_api_key(authorization, is_sandbox)
    body = await parse_json_body(request)
    client_ip = extract_client_ip(request)

    return await OrchestrationService.handle_video_extension(
        api_key=api_key,
        body=body,
        background_tasks=background_tasks,
        ip_address=client_ip,
        sandbox=is_sandbox,
    )


@app.get("/v1/videos/{video_id}")
async def get_video_status(
    video_id: str,
    model: str = Query(..., description="Model name used for the video generation"),
    authorization: str = Header(None),
    sandbox: str = Header(None, alias="x-sandbox"),
):
    """
    Get video generation status and retrieve completed video.
    Poll this endpoint after initiating video generation.
    """
    is_sandbox = sandbox.lower() == "true" if sandbox else False
    api_key = extract_api_key(authorization, is_sandbox)

    return await OrchestrationService.handle_video_status(
        api_key=api_key,
        video_id=video_id,
        model=model,
        sandbox=is_sandbox,
    )

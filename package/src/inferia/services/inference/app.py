"""
Inference Service - Client-Facing API
Proxies requests to the Filtration service for security and policy enforcement,
then routes to the actual model provider.
"""

import logging
from typing import Optional

from inferia.common.schemas.common import HealthCheckResponse
from inferia.services.inference.client import api_gateway_client
from inferia.services.inference.config import settings
from inferia.services.inference.core.http_client import http_client
from inferia.services.inference.core.orchestrator import OrchestrationService
from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware

from inferia.common.exception_handlers import register_exception_handlers
from inferia.common.logger import setup_logging
from inferia.common.app_setup import setup_cors, add_standard_health_routes

# Configure logging
setup_logging(
    level="INFO",
    service_name="inference-gateway",
    use_json=not settings.is_development
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="Inference Gateway - OpenAI Compatible Endpoint",
)

# Register standard exception handlers
register_exception_handlers(app)

# CORS configuration (Standardized)
setup_cors(app, settings.allowed_origins, settings.is_development)


# Add standard / and /health routes
add_standard_health_routes(
    app=app,
    app_name=settings.app_name,
    app_version=settings.app_version,
    environment=settings.environment
)


@app.on_event("shutdown")
async def shutdown_event():
    await http_client.close_client()
    await api_gateway_client.close_client()


def extract_api_key(authorization: str) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid API Key format")
    return authorization.split(" ")[1]


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


# stream_with_tracking removed - logic moved to core.orchestrator.OrchestrationService


@app.get("/v1/models")
async def list_models(authorization: str = Header(None)):
    """
    List available models.
    """
    api_key = extract_api_key(authorization)
    return await OrchestrationService.list_models(api_key)


@app.post("/v1/chat/completions")
async def create_completion(
    request: Request,
    background_tasks: BackgroundTasks,
    authorization: str = Header(None),
):
    """
    Main chat completion endpoint.
    Delegates orchestration to OrchestrationService.
    """
    api_key = extract_api_key(authorization)
    body = await request.json()
    client_ip = extract_client_ip(request)

    return await OrchestrationService.handle_completion(
        api_key=api_key,
        body=body,
        background_tasks=background_tasks,
        ip_address=client_ip,
    )


@app.post("/v1/embeddings")
async def create_embeddings(
    request: Request,
    background_tasks: BackgroundTasks,
    authorization: str = Header(None),
):
    """
    Embeddings endpoint - OpenAI compatible.
    Supports text embedding models deployed via Infinity or TEI.
    """
    api_key = extract_api_key(authorization)
    body = await request.json()
    client_ip = extract_client_ip(request)

    return await OrchestrationService.handle_embeddings(
        api_key=api_key,
        body=body,
        background_tasks=background_tasks,
        ip_address=client_ip,
    )

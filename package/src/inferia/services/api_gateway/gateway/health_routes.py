"""
System health check routes.
Provides a unified health endpoint that checks all downstream services.
"""

import asyncio
import time
from typing import Dict, List, Optional
import httpx
import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel

from inferia.services.api_gateway.config import settings
from inferia.services.api_gateway.gateway.http_client import gateway_http_client

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Health"])


class ServiceHealth(BaseModel):
    name: str
    status: str
    latency_ms: Optional[float] = None
    error: Optional[str] = None


class SystemHealthResponse(BaseModel):
    status: str
    version: str
    services: List[ServiceHealth]


async def check_service(name: str, url: str, timeout: float = 5.0) -> ServiceHealth:
    """Check health of a single service."""
    start = time.time()
    try:
        client = gateway_http_client.get_service_client()
        response = await client.get(url, timeout=timeout)
        latency = (time.time() - start) * 1000

        if response.status_code == 200:
            return ServiceHealth(
                name=name,
                status="online",
                latency_ms=round(latency, 2),
            )
        else:
            return ServiceHealth(
                name=name,
                status="degraded",
                latency_ms=round(latency, 2),
                error=f"HTTP {response.status_code}",
            )
    except httpx.TimeoutException:
        return ServiceHealth(
            name=name,
            status="offline",
            error="Timeout",
        )
    except Exception as e:
        return ServiceHealth(
            name=name,
            status="offline",
            error=str(e),
        )


@router.get("/health/services")
async def services_health_check():
    """
    Check health of all downstream services.
    Returns status of API Gateway and all connected services.
    """
    services_to_check = [
        ("API Gateway", f"http://localhost:{settings.port}/health"),
        (
            "Inference Gateway",
            f"{settings.inference_url or 'http://localhost:8001'}/health",
        ),
        ("Orchestration", f"{settings.orchestration_url}/health"),
        ("Guardrail Service", f"{settings.guardrail_service_url}/health"),
        ("Data Service", f"{settings.data_service_url}/health"),
    ]

    # Check all services in parallel
    tasks = [check_service(name, url) for name, url in services_to_check]
    results = await asyncio.gather(*tasks)

    # Determine overall status
    all_statuses = [r.status for r in results]
    if all(s == "online" for s in all_statuses):
        overall_status = "healthy"
    elif any(s == "offline" for s in all_statuses):
        overall_status = "degraded"
    else:
        overall_status = "partial"

    response = SystemHealthResponse(
        status=overall_status,
        version=settings.app_version,
        services=list(results),
    )

    return JSONResponse(content=jsonable_encoder(response))

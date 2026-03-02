"""
Guardrail Service - LLM Safety Scanning.
"""

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
import logging
from contextlib import asynccontextmanager

from inferia.common.exception_handlers import register_exception_handlers
from inferia.common.logger import setup_logging
from inferia.common.app_setup import setup_cors, add_standard_health_routes
from inferia.services.guardrail.config import settings
from inferia.services.guardrail.engine import guardrail_engine
from inferia.services.guardrail.models import GuardrailResult, ScanType
from inferia.services.guardrail.middleware import internal_auth_middleware

# Configure logging
setup_logging(
    level=settings.log_level,
    service_name="guardrail-service",
    use_json=not settings.is_development
)
logger = logging.getLogger("guardrail-service")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager for startup and shutdown events."""
    logger.info(f"Starting {settings.app_name} v{settings.app_version}")

    # Start polling config from Filtration Service
    from inferia.services.guardrail.config_manager import config_manager

    config_manager.start_polling(
        gateway_url=settings.api_gateway_url,
        api_key=settings.internal_api_key,
    )

    yield

    logger.info(f"Shutting down {settings.app_name}")
    config_manager.stop_polling()


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="Guardrail Service - LLM Safety Scanning",
    lifespan=lifespan,
)

# Register standard exception handlers
register_exception_handlers(app)

# CORS configuration (Standardized)
setup_cors(app, settings.allowed_origins, settings.is_development)

# Add internal authentication middleware
app.middleware("http")(internal_auth_middleware)


class ScanRequest(BaseModel):
    text: str
    scan_type: ScanType = ScanType.INPUT
    user_id: Optional[str] = None
    context: Optional[str] = None  # For output scan
    config: Optional[Dict[str, Any]] = None
    custom_banned_keywords: Optional[List[str]] = None
    pii_entities: Optional[List[str]] = None


# Add standard / and /health routes
add_standard_health_routes(
    app=app,
    app_name=settings.app_name,
    app_version=settings.app_version,
    environment=settings.environment
)


@app.post("/scan", response_model=GuardrailResult, tags=["Guardrail"])
async def scan(request: ScanRequest):
    """
    Scan text for safety violations.
    """
    try:
        if request.scan_type == ScanType.INPUT:
            result = await guardrail_engine.scan_input(
                prompt=request.text,
                user_id=str(request.user_id) if request.user_id else "unknown",
                custom_keywords=request.custom_banned_keywords or [],
                pii_entities=request.pii_entities or [],
                config=request.config or {},
            )
        else:
            result = await guardrail_engine.scan_output(
                prompt=request.context or "",
                output=request.text,
                user_id=str(request.user_id) if request.user_id else "unknown",
                custom_keywords=request.custom_banned_keywords or [],
                pii_entities=request.pii_entities or [],
                config=request.config or {},
            )
        return result
    except Exception as e:
        logger.error(f"Scan failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app:app",
        host=settings.host,
        port=settings.port,
        reload=settings.reload,
        log_level=settings.log_level.lower(),
    )

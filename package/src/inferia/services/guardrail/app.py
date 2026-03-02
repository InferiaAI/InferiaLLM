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

# CORS configuration
_allow_origins = [
    origin.strip() for origin in settings.allowed_origins.split(",") if origin.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allow_origins if not settings.is_development else ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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


@app.get("/", tags=["Root"])
async def root():
    """Root endpoint."""
    return {
        "service": settings.app_name,
        "version": settings.app_version,
        "health": "/health",
        "docs": "/docs",
    }


@app.get("/health", tags=["Health"])
async def health():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "service": settings.app_name,
        "version": settings.app_version,
    }


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

"""
API Gateway Service Application Entry Point.

This is the main entry point for the API Gateway that includes:
- API Gateway functionality (routing, proxying)
- RBAC & Authentication
- Rate Limiting
- Request routing to downstream services (Orchestration, etc.)
"""

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.encoders import jsonable_encoder
from contextlib import asynccontextmanager

from inferia.common.exception_handlers import register_exception_handlers
import logging
import sys

from inferia.common.logger import setup_logging
from inferia.services.api_gateway.config import settings
from inferia.services.api_gateway.models import HealthCheckResponse, ErrorResponse
from inferia.services.api_gateway.gateway.middleware import (
    RequestIDMiddleware,
    StandardHeadersMiddleware,
    ProcessingTimeMiddleware,
)
from inferia.services.api_gateway.gateway.internal_middleware import (
    internal_api_key_middleware,
)
from inferia.services.api_gateway.rbac.middleware import auth_middleware
from inferia.services.api_gateway.rbac.router import router as auth_router
from inferia.services.api_gateway.gateway.router import router as gateway_router
from inferia.services.api_gateway.management.router import router as management_router
from inferia.services.api_gateway.rbac.roles_router import router as roles_router
from inferia.services.api_gateway.rbac.users_router import router as users_router
from inferia.services.api_gateway.audit.router import router as audit_router
from inferia.services.api_gateway.gateway.proxy_routes import router as proxy_router
from inferia.services.api_gateway.gateway.health_routes import router as health_router

# Configure logging
setup_logging(
    level=settings.log_level,
    service_name="api-gateway",
    use_json=not settings.is_development,
    log_file="debug.log"
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager for startup and shutdown events."""
    # Startup
    logger.info(f"Starting {settings.app_name} v{settings.app_version}")
    logger.info(f"Environment: {settings.environment}")
    logger.info(
        f"Rate limiting: {'enabled' if settings.rate_limit_enabled else 'disabled'}"
    )

    # Initialize Default Org & Superadmin
    from inferia.services.api_gateway.db.database import AsyncSessionLocal
    from inferia.services.api_gateway.rbac.initialization import initialize_default_org

    async with AsyncSessionLocal() as session:
        await initialize_default_org(session)

    # Start Config Polling
    from inferia.services.api_gateway.management.config_manager import config_manager

    await config_manager.initialize()
    config_manager.start_polling()

    yield
    # Shutdown
    logger.info(f"Shutting down {settings.app_name}")
    config_manager.stop_polling()

    from inferia.services.api_gateway.gateway.http_client import gateway_http_client
    from inferia.services.api_gateway.gateway.rate_limiter import rate_limiter

    await gateway_http_client.close_all()
    await rate_limiter.close()


# Create FastAPI app
app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="API Gateway Service",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# Register standard exception handlers
register_exception_handlers(app)


# ==================== CORS Configuration ====================

# Parse allowed origins from comma-separated string
allowed_origins = [origin.strip() for origin in settings.allowed_origins.split(",")]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins if not settings.is_development else ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ==================== Custom Middleware ====================

# Add custom middleware in order (last added = first executed)
app.add_middleware(ProcessingTimeMiddleware)
app.add_middleware(StandardHeadersMiddleware)
app.add_middleware(RequestIDMiddleware)

# Add internal API key validation for /internal/* endpoints
app.middleware("http")(internal_api_key_middleware)

# Add RBAC auth middleware
app.middleware("http")(auth_middleware)


# ==================== Exception Handlers ====================


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Global exception handler for unhandled exceptions."""
    logger.error(f"Unhandled exception: {exc}", exc_info=True)

    request_id = getattr(request.state, "request_id", "unknown")

    error_response = ErrorResponse(
        error="internal_server_error",
        message="An unexpected error occurred",
        request_id=request_id,
    )

    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=jsonable_encoder(error_response),
    )


# ==================== Routes ====================


@app.get("/", tags=["Root"])
async def root():
    """Root endpoint."""
    return {
        "service": settings.app_name,
        "version": settings.app_version,
        "environment": settings.environment,
        "docs": "/docs",
        "health": "/health",
    }


@app.get("/health", tags=["Health"])
async def health_check():
    """Health check endpoint."""
    response = HealthCheckResponse(
        status="healthy",
        version=settings.app_version,
        components={
            "rbac": "healthy",
            "rate_limiter": "healthy",
        },
    )
    return JSONResponse(content=jsonable_encoder(response))


# Include routers
app.include_router(auth_router)
app.include_router(audit_router)
app.include_router(management_router)
app.include_router(gateway_router)
app.include_router(roles_router)
app.include_router(users_router)
app.include_router(proxy_router)
app.include_router(health_router)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app:app",
        host=settings.host,
        port=settings.port,
        reload=settings.reload,
        workers=settings.workers,
        log_level=settings.log_level.lower(),
    )

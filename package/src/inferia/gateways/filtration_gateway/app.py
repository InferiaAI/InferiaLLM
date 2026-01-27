"""
Filtration Gateway Application Entry Point.

This is the main entry point for the filtration layer that includes:
- API Gateway functionality
- RBAC & Authentication
- Rate Limiting
- Request routing to orchestration layer
"""

import sys
from pathlib import Path

# Add services to path so we can import from them
# In package: gateways/filtration_gateway -> ../../services/filtration
services_path = Path(__file__).parent.parent.parent / "services" / "filtration"
sys.path.insert(0, str(services_path))

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.encoders import jsonable_encoder
from contextlib import asynccontextmanager
import logging
from datetime import datetime

from config import settings

# Configure logging
logging.basicConfig(
    level=getattr(logging, settings.log_level),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("debug.log"),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

from models import HealthCheckResponse, ErrorResponse
from gateway.middleware import (
    RequestIDMiddleware,
    StandardHeadersMiddleware,
    ProcessingTimeMiddleware
)
from gateway.internal_middleware import internal_api_key_middleware
from rbac.middleware import auth_middleware
from rbac.router import router as auth_router
from gateway.router import router as gateway_router
from management.router import router as management_router
from rbac.roles_router import router as roles_router
from rbac.users_router import router as users_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager for startup and shutdown events."""
    # Startup
    logger.info(f"Starting {settings.app_name} v{settings.app_version}")
    logger.info(f"Environment: {settings.environment}")
    logger.info(f"Rate limiting: {'enabled' if settings.rate_limit_enabled else 'disabled'}")

    # Initialize Default Org & Superadmin
    from db.database import AsyncSessionLocal
    from rbac.initialization import initialize_default_org
    
    async with AsyncSessionLocal() as session:
        await initialize_default_org(session)

    # Start Config Polling
    from management.config_manager import config_manager
    await config_manager.initialize()
    
    # Sync dependent services
    from guardrail.config import guardrail_settings
    guardrail_settings.refresh_from_main_settings()
    
    config_manager.start_polling()
    
    yield
    # Shutdown
    logger.info(f"Shutting down {settings.app_name}")
    config_manager.stop_polling()


# Create FastAPI app
app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="Filtration Layer for InferiaLLM - API Gateway, RBAC, and Policy Enforcement",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)


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
        request_id=request_id
    )
    
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=jsonable_encoder(error_response)
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
        "health": "/health"
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
        }
    )
    return JSONResponse(content=jsonable_encoder(response))


# Include routers
from data.router import router as data_router
from audit.router import router as audit_router

app.include_router(auth_router)
app.include_router(audit_router)
app.include_router(management_router)
app.include_router(data_router)
app.include_router(gateway_router)
app.include_router(roles_router)
app.include_router(users_router)


# ==================== Run Application ====================

if __name__ == "__main__":
    import uvicorn
    
    uvicorn.run(
        "app:app",
        host=settings.host,
        port=settings.port,
        reload=settings.reload,
        log_level=settings.log_level.lower()
    )

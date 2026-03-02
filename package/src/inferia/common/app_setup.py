"""
Standard FastAPI application setup utilities.
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from inferia.common.schemas.common import HealthCheckResponse

def setup_cors(app: FastAPI, allowed_origins_str: str, is_development: bool = False):
    """
    Standard securely configured CORS middleware for InferiaLLM.
    """
    # Parse allowed origins from comma-separated string
    if not allowed_origins_str:
        allowed_origins = []
    else:
        allowed_origins = [
            origin.strip() for origin in allowed_origins_str.split(",") if origin.strip()
        ]

    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins if not is_development else ["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

def add_standard_health_routes(app: FastAPI, app_name: str, app_version: str, environment: str, extra_components: dict = None):
    """
    Add standard / and /health routes to the FastAPI application.
    """
    
    @app.get("/", tags=["Root"])
    async def root():
        """Standard root endpoint."""
        return {
            "service": app_name,
            "version": app_version,
            "environment": environment,
            "docs": "/docs",
            "health": "/health",
        }

    @app.get("/health", tags=["Health"], response_model=HealthCheckResponse)
    async def health_check():
        """Standard health check endpoint."""
        return HealthCheckResponse(
            status="healthy",
            version=app_version,
            service=app_name,
            components=extra_components or {}
        )

"""
Orchestration Gateway Configuration

Settings loaded from environment variables with sensible defaults.
"""

import os
from dataclasses import dataclass


@dataclass
class Settings:
    """Application settings."""
    
    # App info
    app_name: str = "Orchestration Gateway"
    app_version: str = "1.0.0"
    environment: str = os.getenv("ENVIRONMENT", "development")
    
    # Server settings
    host: str = os.getenv("HOST", "0.0.0.0")
    http_port: int = int(os.getenv("HTTP_PORT") or "8080")
    grpc_port: int = int(os.getenv("GRPC_PORT") or "50051")
    
    # Database
    postgres_dsn: str = os.getenv(
        "DATABASE_URL",
        os.getenv(
            "POSTGRES_DSN",
            "postgresql://inferia:inferia@localhost:5432/inferia"
        )
    )
    
    # Cleanup DSN to ensure it's standard postgresql://
    def __post_init__(self):
        if self.postgres_dsn.startswith("postgresql+asyncpg://"):
            self.postgres_dsn = self.postgres_dsn.replace("postgresql+asyncpg://", "postgresql://", 1)
    
    # Redis
    redis_host: str = os.getenv("REDIS_HOST", "localhost")
    redis_port: int = int(os.getenv("REDIS_PORT") or "6379")
    redis_username: str = os.getenv("REDIS_USERNAME", "")
    redis_password: str = os.getenv("REDIS_PASSWORD", "")
    
    # Nosana
    nosana_sidecar_url: str = os.getenv("NOSANA_SIDECAR_URL", "http://localhost:3000")
    nosana_internal_api_key: str = os.getenv("NOSANA_INTERNAL_API_KEY", "nos-internal-secret-change-in-prod")
    
    @property
    def is_development(self) -> bool:
        return self.environment == "development"


settings = Settings()

"""
Configuration for Inference Gateway.
"""

from typing import Any
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Inference Gateway settings."""

    # Application Settings
    app_name: str = "InferiaLLM Inference Gateway"
    app_version: str = "0.1.0"
    environment: str = "development"

    # Server Settings
    host: str = "0.0.0.0"
    port: int = 8001
    reload: bool = False
    log_level: str = "INFO"

    # API Gateway Settings
    # In production, use HTTPS URLs with valid SSL certificates
    api_gateway_url: str = Field(
        default="http://localhost:8000",
        alias="API_GATEWAY_URL",
        validation_alias="API_GATEWAY_URL",
    )
    api_gateway_internal_key: str = Field(
        default="dev-internal-key-change-in-prod",
        alias="INTERNAL_API_KEY",
        validation_alias="INTERNAL_API_KEY",
    )

    # CORS Settings
    # In production, set ALLOWED_ORIGINS to specific domains (comma-separated)
    # Example: "https://app.inferia.ai,https://admin.inferia.ai"
    # Default is restrictive - only allow localhost origins
    allowed_origins: str = Field(
        default="http://localhost:3000,http://localhost:3001,http://localhost:5173,http://localhost:8001",
        alias="ALLOWED_ORIGINS",
        validation_alias="ALLOWED_ORIGINS",
    )

    # SSL/TLS Configuration for service communication
    verify_ssl: bool = Field(
        default=True,
        alias="VERIFY_SSL",
        validation_alias="VERIFY_SSL",
        description="Verify SSL certificates for HTTPS service calls",
    )

    # Timeouts
    request_timeout: int = 30

    # Context Cache Settings
    # Cache duration for resolved API key contexts (deployment, guardrails, etc.)
    context_cache_ttl: int = Field(
        default=30,
        alias="CONTEXT_CACHE_TTL",
        validation_alias="CONTEXT_CACHE_TTL",
        description="TTL in seconds for API key context cache",
    )
    context_cache_maxsize: int = Field(
        default=1000,
        alias="CONTEXT_CACHE_MAXSIZE",
        validation_alias="CONTEXT_CACHE_MAXSIZE",
        description="Maximum number of entries in API key context cache",
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",  # Ignore extra env vars from shared .env file
    )


settings = Settings()

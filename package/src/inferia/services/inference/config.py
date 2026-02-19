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
    workers: int = Field(
        default=1,
        alias="INFERENCE_WORKERS",
        validation_alias="INFERENCE_WORKERS",
    )
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

    # Upstream HTTP Client Tuning
    upstream_http_timeout_seconds: float = Field(
        default=60.0,
        alias="UPSTREAM_HTTP_TIMEOUT_SECONDS",
        validation_alias="UPSTREAM_HTTP_TIMEOUT_SECONDS",
    )
    upstream_http_connect_timeout_seconds: float = Field(
        default=10.0,
        alias="UPSTREAM_HTTP_CONNECT_TIMEOUT_SECONDS",
        validation_alias="UPSTREAM_HTTP_CONNECT_TIMEOUT_SECONDS",
    )
    upstream_http_max_connections: int = Field(
        default=500,
        alias="UPSTREAM_HTTP_MAX_CONNECTIONS",
        validation_alias="UPSTREAM_HTTP_MAX_CONNECTIONS",
    )
    upstream_http_max_keepalive_connections: int = Field(
        default=100,
        alias="UPSTREAM_HTTP_MAX_KEEPALIVE_CONNECTIONS",
        validation_alias="UPSTREAM_HTTP_MAX_KEEPALIVE_CONNECTIONS",
    )

    # Upstream Concurrency Guards
    upstream_global_max_in_flight: int = Field(
        default=0,
        alias="UPSTREAM_GLOBAL_MAX_IN_FLIGHT",
        validation_alias="UPSTREAM_GLOBAL_MAX_IN_FLIGHT",
        description="0 disables global limit",
    )
    upstream_per_deployment_max_in_flight: int = Field(
        default=64,
        alias="UPSTREAM_PER_DEPLOYMENT_MAX_IN_FLIGHT",
        validation_alias="UPSTREAM_PER_DEPLOYMENT_MAX_IN_FLIGHT",
    )
    upstream_slot_acquire_timeout_seconds: float = Field(
        default=1.0,
        alias="UPSTREAM_SLOT_ACQUIRE_TIMEOUT_SECONDS",
        validation_alias="UPSTREAM_SLOT_ACQUIRE_TIMEOUT_SECONDS",
    )

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

    # Quota Check Cache Settings
    # A short positive-cache reduces internal gateway pressure under concurrency spikes.
    quota_check_cache_ttl_seconds: float = Field(
        default=1.0,
        alias="QUOTA_CHECK_CACHE_TTL_SECONDS",
        validation_alias="QUOTA_CHECK_CACHE_TTL_SECONDS",
        description="TTL in seconds for successful quota checks",
    )
    quota_check_cache_maxsize: int = Field(
        default=10000,
        alias="QUOTA_CHECK_CACHE_MAXSIZE",
        validation_alias="QUOTA_CHECK_CACHE_MAXSIZE",
        description="Maximum number of entries in successful quota check cache",
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",  # Ignore extra env vars from shared .env file
    )


settings = Settings()

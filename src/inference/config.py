"""
Configuration for Inference Gateway.
"""

from typing import Any, Optional
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Inference Gateway settings.

    Source precedence (highest → lowest): init/CLI > env > .env > pydantic defaults.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @field_validator("allowed_origins", mode="before")
    @classmethod
    def _coerce_allowed_origins(cls, v: Any) -> Any:
        if isinstance(v, list):
            return ",".join(str(item).strip() for item in v if str(item).strip())
        return v

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
    # Example: "https://app.ai,https://admin.ai"
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

    # External Provider Proxy (e.g., InferiaGate)
    # When set, all external provider requests (OpenAI, Anthropic, etc.) are
    # routed through this URL instead of directly to the provider's API.
    # The proxy must accept OpenAI-compatible /v1/chat/completions format.
    # Set to empty string or omit to use direct provider connections.
    external_proxy_url: Optional[str] = Field(
        default=None,
        alias="EXTERNAL_PROXY_URL",
        validation_alias="EXTERNAL_PROXY_URL",
        description="URL of external LLM proxy (e.g., InferiaGate). Routes all external provider traffic through this proxy.",
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
    upstream_video_timeout_seconds: float = Field(
        default=300.0,
        alias="UPSTREAM_VIDEO_TIMEOUT_SECONDS",
        validation_alias="UPSTREAM_VIDEO_TIMEOUT_SECONDS",
        description="Timeout for video generation requests (default 5 minutes)",
    )

    # Redis Settings (for rate limiting)
    redis_host: str = Field(
        default="localhost",
        alias="REDIS_HOST",
        validation_alias="REDIS_HOST",
    )
    redis_port: int = Field(
        default=6379,
        alias="REDIS_PORT",
        validation_alias="REDIS_PORT",
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

    # API Gateway HTTP Client Pool (for context/quota/guardrail calls)
    gateway_http_max_connections: int = Field(
        default=1000,
        alias="GATEWAY_HTTP_MAX_CONNECTIONS",
        validation_alias="GATEWAY_HTTP_MAX_CONNECTIONS",
        description="Max connections to API Gateway",
    )
    gateway_http_max_keepalive_connections: int = Field(
        default=100,
        alias="GATEWAY_HTTP_MAX_KEEPALIVE_CONNECTIONS",
        validation_alias="GATEWAY_HTTP_MAX_KEEPALIVE_CONNECTIONS",
        description="Max keepalive connections to API Gateway",
    )

    # Upstream Concurrency Guards
    upstream_global_max_in_flight: int = Field(
        default=0,
        alias="UPSTREAM_GLOBAL_MAX_IN_FLIGHT",
        validation_alias="UPSTREAM_GLOBAL_MAX_IN_FLIGHT",
        description="0 disables global limit",
    )
    upstream_per_deployment_max_in_flight: int = Field(
        default=100,
        alias="UPSTREAM_PER_DEPLOYMENT_MAX_IN_FLIGHT",
        validation_alias="UPSTREAM_PER_DEPLOYMENT_MAX_IN_FLIGHT",
    )
    upstream_slot_acquire_timeout_seconds: float = Field(
        default=20.0,
        alias="UPSTREAM_SLOT_ACQUIRE_TIMEOUT_SECONDS",
        validation_alias="UPSTREAM_SLOT_ACQUIRE_TIMEOUT_SECONDS",
    )

    # Upstream Security
    upstream_allowed_internal_hosts: str = Field(
        default="",
        alias="UPSTREAM_ALLOWED_INTERNAL_HOSTS",
        validation_alias="UPSTREAM_ALLOWED_INTERNAL_HOSTS",
        description="Comma-separated hostnames that bypass private-IP SSRF checks",
    )
    upstream_max_response_bytes: int = Field(
        default=52_428_800,
        alias="UPSTREAM_MAX_RESPONSE_BYTES",
        validation_alias="UPSTREAM_MAX_RESPONSE_BYTES",
        description="Maximum upstream response body size (default 50MB)",
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

    # JWT Settings (for sandbox mode)
    jwt_secret_key: str = Field(
        default="placeholder-secret-key-at-least-32-chars-long",
        alias="JWT_SECRET_KEY",
        validation_alias="JWT_SECRET_KEY",
    )
    jwt_algorithm: str = "HS256"

    # External SSO auth (sandbox JWT verification in oidc/inferiaauth mode).
    # In these modes the dashboard's bearer is an EdDSA JWT issued by the IdP
    # (verified via JWKS), NOT a local HS256 token — so the sandbox path must
    # verify it the same way the api_gateway does. Mirrors api_gateway.config.
    auth_provider: str = Field(default="local", validation_alias="AUTH_PROVIDER")
    external_auth_url: Optional[str] = Field(
        default=None, validation_alias="EXTERNAL_AUTH_URL"
    )
    external_auth_issuer: Optional[str] = Field(
        default=None, validation_alias="EXTERNAL_AUTH_ISSUER"
    )
    app_namespace: str = Field(
        default="inferiallm", validation_alias="APP_NAMESPACE"
    )
    oauth_client_id: Optional[str] = Field(
        default=None, validation_alias="OAUTH_CLIENT_ID"
    )
    oauth_jwks_cache_ttl_seconds: int = Field(
        default=3600, ge=60, le=86400,
        validation_alias="OAUTH_JWKS_CACHE_TTL_SECONDS",
    )
    verify_ssl: bool = Field(default=True, validation_alias="VERIFY_SSL")
    ssl_ca_bundle: Optional[str] = Field(
        default=None, validation_alias="SSL_CA_BUNDLE"
    )

    @property
    def is_external_mode(self) -> bool:
        """True when auth is delegated to an external IdP (oidc/inferiaauth)."""
        return self.auth_provider in ("oidc", "inferiaauth", "external")

    @property
    def httpx_verify(self) -> object:
        """httpx ``verify=`` value: CA-bundle path if set, else the bool."""
        return self.ssl_ca_bundle or self.verify_ssl

    @property
    def is_production(self) -> bool:
        """Check if running in production environment."""
        return self.environment == "production"

    @property
    def is_development(self) -> bool:
        """Check if running in development environment."""
        return self.environment == "development"



settings = Settings()

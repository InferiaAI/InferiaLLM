"""
Configuration management for the Filtration Layer.
Uses Pydantic Settings for environment-based configuration.
"""

from typing import ClassVar, Literal, Optional, Any, Dict, List
import logging
from pydantic import Field, BaseModel, field_validator
from pydantic_settings import SettingsConfigDict
from inferia.common.unified_config import UnifiedBaseSettings
from inferia.common.unified_config.schema import (
    ProvidersConfig,
    AWSProvider,
    GCPProvider,
    AzureProvider,
    IBMProvider,
    NosanaProvider,
    NosanaApiKeyEntry,
)

logger = logging.getLogger(__name__)

# --- Provider Credential Model ---


class ProviderCredential(BaseModel):
    """Generic provider credential that works for any provider.

    Examples:
    - provider="nosana", credential_type="api_key", name="prod"
    - provider="aws", credential_type="access_key_id", name="default"
    - provider="gcp", credential_type="service_account_json", name="default"
    """

    provider: str  # e.g., 'nosana', 'aws', 'gcp', 'azure', 'ibm'
    name: str
    credential_type: str  # e.g., 'api_key', 'wallet_private_key', 'access_key_id'
    value: str
    is_active: bool = True


# --- Main Settings ---


class Settings(UnifiedBaseSettings):
    """Application settings loaded from yaml, env, or defaults.

    Source precedence (highest → lowest): init/CLI > env > .env > yaml > pydantic defaults.
    See docs/superpowers/specs/2026-05-12-unified-config-design.md.
    """

    _yaml_path: ClassVar[str] = "services.api_gateway"


    # Application Settings
    app_name: str = "InferiaLLM API Gateway"
    app_version: str = "0.1.0"
    environment: Literal["development", "staging", "production"] = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    logstash_host: Optional[str] = Field(default=None, validation_alias="LOGSTASH_HOST")
    logstash_port: int = Field(default=5959, validation_alias="LOGSTASH_PORT")

    # Server Settings
    host: str = "0.0.0.0"
    port: int = 8000
    reload: bool = Field(default=False, validation_alias="DEBUG_RELOAD")
    workers: int = Field(default=1, validation_alias="API_GATEWAY_WORKERS")

    # Reverse Proxy Settings
    # Enable proxy_headers so uvicorn trusts X-Forwarded-For from allowed proxies.
    # FORWARDED_ALLOW_IPS: comma-separated IPs/CIDRs of trusted proxies, or "*" to trust all.
    # Default "" disables proxy header processing (safe for direct-to-internet deployments).
    proxy_headers: bool = Field(default=True, validation_alias="PROXY_HEADERS")
    forwarded_allow_ips: Optional[str] = Field(
        default=None, validation_alias="FORWARDED_ALLOW_IPS"
    )

    # Multi-tenancy / Organization Settings
    default_org_name: str = "Default Organization"

    # External Auth Provider (inferia-auth)
    # Set to "external" to delegate authentication to inferia-auth service.
    # Superadmin login always works locally regardless of this setting.
    auth_provider: Literal["local", "external"] = Field(
        default="local", validation_alias="AUTH_PROVIDER"
    )
    external_auth_url: Optional[str] = Field(
        default=None,
        validation_alias="EXTERNAL_AUTH_URL",
        description="Base URL of the inferia-auth service (e.g. http://inferia-auth:3000)",
    )

    # Superadmin credentials - MUST be set via environment variables in production
    superadmin_email: Optional[str] = Field(default=None, validation_alias="SUPERADMIN_EMAIL")
    superadmin_password: Optional[str] = Field(
        default=None, validation_alias="SUPERADMIN_PASSWORD"
    )

    # Internal API Key (for service-to-service auth) - MUST be set in production
    # Generate with: openssl rand -hex 32
    internal_api_key: Optional[str] = Field(
        default=None, min_length=32, validation_alias="INTERNAL_API_KEY"
    )
    allowed_origins: str = "http://localhost:3001,http://localhost:8001,http://localhost:5173"  # Comma-separated list

    @field_validator("allowed_origins", mode="before")
    @classmethod
    def _coerce_allowed_origins(cls, v: Any) -> Any:
        # yaml provides this as a list (security.allowed_origins: list[str]); the
        # legacy env-driven path provides a comma-separated string. setup_cors()
        # consumes a string and splits on ',' — normalize lists here so both
        # sources work transparently.
        if isinstance(v, list):
            return ",".join(str(item).strip() for item in v if str(item).strip())
        return v

    # RBAC Settings
    jwt_secret_key: str = Field(
        default="placeholder-secret-key-at-least-32-chars-long", 
        min_length=32, 
        validation_alias="JWT_SECRET_KEY"
    )
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 7

    # Rate Limiting Settings
    rate_limit_enabled: bool = False
    rate_limit_requests_per_minute: int = 10000
    rate_limit_burst_size: int = 1000
    redis_url: Optional[str] = Field(default=None, validation_alias="REDIS_URL")
    redis_host: str = Field(default="localhost", validation_alias="REDIS_HOST")
    redis_port: int = Field(default=6379, validation_alias="REDIS_PORT")
    redis_db: str = Field(default="0", validation_alias="REDIS_DB")
    redis_username: Optional[str] = Field(default=None, validation_alias="REDIS_USERNAME")
    redis_password: Optional[str] = Field(default=None, validation_alias="REDIS_PASSWORD")
    redis_ssl: bool = Field(default=False, validation_alias="REDIS_SSL")
    use_redis_rate_limit: bool = False

    # Internal HTTP Client Tuning
    service_http_timeout_seconds: float = 10.0
    service_http_connect_timeout_seconds: float = 3.0
    service_http_max_connections: int = 500
    service_http_max_keepalive_connections: int = 100

    proxy_http_timeout_seconds: float = 300.0
    proxy_http_max_connections: int = 500
    proxy_http_max_keepalive_connections: int = 100

    # Database Settings
    # In production, use strong credentials and enable SSL
    # Example: postgresql+asyncpg://user:strong_password@host:5432/dbname?sslmode=require
    database_url: str = Field(
        default="postgresql+asyncpg://inferia:inferia@localhost:5432/inferia",
        validation_alias="DATABASE_URL",
    )

    # LLM Settings
    openai_api_key: Optional[str] = None

    # Security / Encryption
    log_encryption_key: Optional[str] = Field(
        default=None, description="32-byte hex key for log encryption"
    )
    secret_encryption_key: Optional[str] = Field(
        default=None, validation_alias="SECRET_ENCRYPTION_KEY"
    )

    # Service URLs (Microservices)
    # In production, these should use HTTPS with valid certificates
    guardrail_service_url: str = Field(
        default="http://localhost:8002", validation_alias="GUARDRAIL_SERVICE_URL"
    )
    data_service_url: str = Field(
        default="http://localhost:8003", validation_alias="DATA_SERVICE_URL"
    )
    orchestration_url: str = Field(
        default="http://localhost:8080", validation_alias="ORCHESTRATION_URL"
    )
    inference_url: str = Field(
        default="http://localhost:8001", validation_alias="INFERENCE_URL"
    )

    # SSL/TLS Configuration for service communication
    verify_ssl: bool = Field(
        default=True,
        validation_alias="VERIFY_SSL",
        description="Verify SSL certificates for HTTPS service calls",
    )
    ssl_ca_bundle: Optional[str] = Field(
        default=None,
        validation_alias="SSL_CA_BUNDLE",
        description="Path to custom CA bundle for SSL verification",
    )

    # Infrastructure / Provider Keys (Managed via Dashboard/DB)
    providers: ProvidersConfig = Field(default_factory=ProvidersConfig)

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", case_sensitive=False, extra="ignore"
    )

    _PLACEHOLDER_JWT_SECRET = "placeholder-secret-key-at-least-32-chars-long"

    def model_post_init(self, __context: Any) -> None:
        """
        Initialization logic.
        Note: DB config loading is handled by ConfigManager asynchronously.
        """
        if self.jwt_secret_key == self._PLACEHOLDER_JWT_SECRET:
            if self.is_production:
                raise RuntimeError(
                    "FATAL: JWT_SECRET_KEY is set to the default placeholder. "
                    "Set a strong secret via environment variable before running in production. "
                    "Generate one with: openssl rand -hex 32"
                )
            else:
                logger.warning(
                    "JWT_SECRET_KEY is using the default placeholder. "
                    "This is insecure and must not be used in production. "
                    "Generate a secret with: openssl rand -hex 32"
                )

    @property
    def resolved_redis_url(self) -> str:
        """Resolve a concrete Redis URL.

        Prefers an explicit REDIS_URL; otherwise builds one from REDIS_HOST/PORT/DB
        plus optional REDIS_USERNAME/REDIS_PASSWORD and REDIS_SSL.
        """
        if self.redis_url:
            return self.redis_url
        scheme = "rediss" if self.redis_ssl else "redis"
        auth = ""
        if self.redis_password:
            user = self.redis_username or ""
            auth = f"{user}:{self.redis_password}@"
        return f"{scheme}://{auth}{self.redis_host}:{self.redis_port}/{self.redis_db}"

    @property
    def sqlalchemy_database_url(self) -> str:
        """Ensure the URL has the asyncpg driver prefix."""
        url = self.database_url
        if url.startswith("postgresql://"):
            return url.replace("postgresql://", "postgresql+asyncpg://", 1)
        return url

    @property
    def is_production(self) -> bool:
        """Check if running in production environment."""
        return self.environment == "production"

    @property
    def is_development(self) -> bool:
        """Check if running in development environment."""
        return self.environment == "development"


# Global settings instance
settings = Settings()

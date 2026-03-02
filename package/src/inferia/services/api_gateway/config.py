"""
Configuration management for the Filtration Layer.
Uses Pydantic Settings for environment-based configuration.
"""

from typing import Literal, Optional, Any, Dict, List
from pydantic import Field, BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict

# --- Nested Configuration Models ---


class AWSConfig(BaseModel):
    access_key_id: Optional[str] = None
    secret_access_key: Optional[str] = None
    region: str = "ap-south-1"


class ChromaConfig(BaseModel):
    api_key: Optional[str] = None
    tenant: Optional[str] = None
    url: Optional[str] = None
    is_local: bool = True
    database: Optional[str] = None


class GroqConfig(BaseModel):
    api_key: Optional[str] = None


class LakeraConfig(BaseModel):
    api_key: Optional[str] = None


class ProviderCredential(BaseModel):
    """Generic provider credential that works for any provider (nosana, akash, etc.)

    Examples:
    - provider="nosana", credential_type="api_key", name="Piyush"
    - provider="akash", credential_type="mnemonic", name="Main Wallet"
    - provider="aws", credential_type="access_key", name="Production"
    """

    provider: str  # e.g., 'nosana', 'akash', 'aws'
    name: str
    credential_type: str  # e.g., 'api_key', 'wallet', 'mnemonic', 'access_key'
    value: str
    is_active: bool = True


class NosanaApiKeyEntry(BaseModel):
    """A single named Nosana API key credential."""
    name: str
    key: str
    is_active: bool = True


class NosanaConfig(BaseModel):
    wallet_private_key: Optional[str] = None
    api_key: Optional[str] = None  # Deprecated: kept for migration
    api_keys: List[NosanaApiKeyEntry] = Field(default_factory=list)  # Named credentials



class AkashConfig(BaseModel):
    mnemonic: Optional[str] = None


class CloudConfig(BaseModel):
    aws: AWSConfig = Field(default_factory=AWSConfig)


class VectorDBConfig(BaseModel):
    chroma: ChromaConfig = Field(default_factory=ChromaConfig)


class GuardrailsConfig(BaseModel):
    groq: GroqConfig = Field(default_factory=GroqConfig)
    lakera: LakeraConfig = Field(default_factory=LakeraConfig)


class DePINConfig(BaseModel):
    nosana: NosanaConfig = Field(default_factory=NosanaConfig)
    akash: AkashConfig = Field(default_factory=AkashConfig)


class ProvidersConfig(BaseModel):
    cloud: CloudConfig = Field(default_factory=CloudConfig)
    vectordb: VectorDBConfig = Field(default_factory=VectorDBConfig)
    guardrails: GuardrailsConfig = Field(default_factory=GuardrailsConfig)
    depin: DePINConfig = Field(default_factory=DePINConfig)


# --- Main Settings ---


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # Application Settings
    app_name: str = "InferiaLLM API Gateway"
    app_version: str = "0.1.0"
    environment: Literal["development", "staging", "production"] = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"

    # Server Settings
    host: str = "0.0.0.0"
    port: int = 8000
    reload: bool = Field(default=False, validation_alias="DEBUG_RELOAD")
    workers: int = Field(default=1, validation_alias="API_GATEWAY_WORKERS")

    # Multi-tenancy / Organization Settings
    default_org_name: str = "Default Organization"

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
    redis_url: str = "redis://localhost:6379/0"
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

    def model_post_init(self, __context: Any) -> None:
        """
        Initialization logic.
        Note: DB config loading is handled by ConfigManager asynchronously.
        """
        pass

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

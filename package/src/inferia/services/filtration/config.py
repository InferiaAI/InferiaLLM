"""
Configuration management for the Filtration Layer.
Uses Pydantic Settings for environment-based configuration.
"""

from typing import Literal, Optional, Any, Dict
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


class NosanaConfig(BaseModel):
    wallet_private_key: Optional[str] = None
    api_key: Optional[str] = None


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
    app_name: str = "InferiaLLM Filtration Layer"
    app_version: str = "0.1.0"
    environment: Literal["development", "staging", "production"] = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"

    # Server Settings
    host: str = "0.0.0.0"
    port: int = 8000
    reload: bool = True

    # Multi-tenancy / Organization Settings
    default_org_name: str = "Default Organization"
    superadmin_email: str = "admin@example.com"
    superadmin_password: str = Field(default="admin123", min_length=1)

    # Internal API Key (for service-to-service auth)
    internal_api_key: str = Field(
        default="dev-internal-key-change-in-prod", min_length=1
    )
    allowed_origins: str = (
        "http://localhost:8001,http://localhost:5173"  # Comma-separated list
    )

    # RBAC Settings
    jwt_secret_key: str = Field(
        default="dev-secret-key-change-in-production", min_length=1
    )
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 7

    # Rate Limiting Settings
    rate_limit_enabled: bool = True
    rate_limit_requests_per_minute: int = 60
    rate_limit_burst_size: int = 10
    redis_url: str = "redis://localhost:6379/0"
    use_redis_rate_limit: bool = False

    # Database Settings
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

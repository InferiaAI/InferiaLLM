"""
Configuration management for the Filtration Layer.
Uses Pydantic Settings for environment-based configuration.
"""

from typing import Literal, Optional
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


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
    superadmin_password: str = Field(..., min_length=1)

    # Internal API Key (for service-to-service auth)
    internal_api_key: str = Field(..., min_length=1)
    allowed_origins: str = "http://localhost:8001,http://localhost:5173"  # Comma-separated list

    # RBAC Settings
    jwt_secret_key: str = Field(..., min_length=1)
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
        validation_alias="DATABASE_URL"
    )

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", case_sensitive=False, extra="ignore"
    )

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

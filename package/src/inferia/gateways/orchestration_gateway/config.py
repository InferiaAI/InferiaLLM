"""
Orchestration Gateway Configuration

Settings loaded from environment variables with sensible defaults.
Updated to use Pydantic Settings and load from ~/.inferia/config.json
"""

import os
from typing import Any
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings."""

    # App info
    app_name: str = "Orchestration Gateway"
    app_version: str = "1.0.0"
    environment: str = Field(default="development", validation_alias="ENVIRONMENT")

    # Server settings
    host: str = Field(default="0.0.0.0", validation_alias="HOST")
    http_port: int = Field(default=8080, validation_alias="HTTP_PORT")
    grpc_port: int = Field(default=50051, validation_alias="GRPC_PORT")

    # Database
    postgres_dsn: str = Field(
        default="postgresql://inferia:inferia@localhost:5432/inferia",
        validation_alias="DATABASE_URL",
    )
    # Pydantic will check DATABASE_URL first.
    # If using POSTGRES_DSN env var, explicit support could be added via alias_priority
    # but Pydantic standardizes on one usually. We'll stick to typical pattern.

    # Redis
    redis_host: str = Field(default="localhost", validation_alias="REDIS_HOST")
    redis_port: int = Field(default=6379, validation_alias="REDIS_PORT")
    redis_username: str = Field(default="", validation_alias="REDIS_USERNAME")
    redis_password: str = Field(default="", validation_alias="REDIS_PASSWORD")

    # Nosana
    nosana_sidecar_url: str = Field(
        default="http://localhost:3000", validation_alias="NOSANA_SIDECAR_URL"
    )

    @property
    def is_development(self) -> bool:
        return self.environment == "development"

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", case_sensitive=False, extra="ignore"
    )


settings = Settings()

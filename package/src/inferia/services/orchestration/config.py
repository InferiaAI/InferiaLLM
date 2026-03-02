"""
Orchestration Service Configuration
"""

import os
from typing import Any, Optional
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ProviderSettings(BaseSettings):
    """Provider-specific configuration settings."""

    # Nosana Configuration
    nosana_sidecar_url: str = Field(
        default="http://localhost:3000", validation_alias="NOSANA_SIDECAR_URL"
    )
    nosana_discovery_url: str = Field(
        default="https://dashboard.k8s.prd.nos.ci/api/markets",
        validation_alias="NOSANA_DISCOVERY_URL",
    )
    nosana_internal_api_key: str = Field(
        default="", validation_alias="NOSANA_INTERNAL_API_KEY"
    )

    # Akash Configuration
    akash_sidecar_url: str = Field(
        default="http://localhost:3000/akash", validation_alias="AKASH_SIDECAR_URL"
    )
    akash_rpc_url: str = Field(
        default="https://rpc.akash.forbole.com:443", validation_alias="AKASH_NODE"
    )
    akash_api_url: str = Field(
        default="https://api.akashnet.net", validation_alias="AKASH_API_URL"
    )

    # Kubernetes Configuration
    k8s_namespace: str = Field(default="default", validation_alias="K8S_NAMESPACE")
    k8s_config_path: Optional[str] = Field(
        default=None, validation_alias="K8S_CONFIG_PATH"
    )

    # AWS/Cloud Configuration
    aws_region: str = Field(default="us-east-1", validation_alias="AWS_REGION")
    aws_access_key_id: str = Field(default="", validation_alias="AWS_ACCESS_KEY_ID")
    aws_secret_access_key: str = Field(
        default="", validation_alias="AWS_SECRET_ACCESS_KEY"
    )


class Settings(BaseSettings):
    """Application settings."""

    # App info
    app_name: str = "Orchestration Service"
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

    # Filtration (shared DB)
    api_gateway_database_url: str = Field(
        default="", validation_alias="API_GATEWAY_DATABASE_URL"
    )

    # Provider Settings (backward compatibility)
    nosana_sidecar_url: str = Field(
        default="http://localhost:3000", validation_alias="NOSANA_SIDECAR_URL"
    )

    internal_api_key: str = Field(default="", validation_alias="INTERNAL_API_KEY")

    # Ephemeral Provider Failure Detection
    ephemeral_failure_threshold_minutes: int = Field(
        default=10, validation_alias="EPHEMERAL_FAILURE_THRESHOLD_MINUTES"
    )

    # Default Provider Settings
    default_readiness_timeout: int = Field(
        default=300, validation_alias="DEFAULT_READINESS_TIMEOUT"
    )
    default_polling_interval: int = Field(
        default=20, validation_alias="DEFAULT_POLLING_INTERVAL"
    )

    @property
    def is_development(self) -> bool:
        return self.environment == "development"

    def get_provider_config(self, provider: str) -> dict:
        """
        Get configuration for a specific provider.

        Args:
            provider: Provider name (e.g., 'nosana', 'akash', 'k8s')

        Returns:
            Dictionary of provider-specific configuration
        """
        provider_configs = {
            "nosana": {
                "sidecar_url": self.nosana_sidecar_url,
                "discovery_url": getattr(
                    self,
                    "nosana_discovery_url",
                    "https://dashboard.k8s.prd.nos.ci/api/markets",
                ),
                "internal_api_key": getattr(
                    self, "nosana_internal_api_key", self.internal_api_key
                ),
            },
            "akash": {
                "sidecar_url": getattr(
                    self, "akash_sidecar_url", "http://localhost:3000/akash"
                ),
                "rpc_url": getattr(
                    self, "akash_rpc_url", "https://rpc.akash.forbole.com:443"
                ),
                "api_url": getattr(self, "akash_api_url", "https://api.akashnet.net"),
            },
            "k8s": {
                "namespace": getattr(self, "k8s_namespace", "default"),
                "config_path": getattr(self, "k8s_config_path", None),
            },
            "aws": {
                "region": getattr(self, "aws_region", "us-east-1"),
            },
        }
        return provider_configs.get(provider, {})

    @property
    def is_production(self) -> bool:
        """Check if running in production environment."""
        return getattr(self, "environment", "development") == "production"

    @property
    def is_development(self) -> bool:
        """Check if running in development environment."""
        return getattr(self, "environment", "development") == "development"

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", case_sensitive=False, extra="ignore"
    )


settings = Settings()

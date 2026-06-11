"""
Orchestration Service Configuration
"""

import os
from typing import Any, ClassVar, Optional
from pydantic import Field
from pydantic_settings import BaseSettings
from common.unified_config import UnifiedBaseSettings


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

    # GCP Configuration
    gcp_project_id: str = Field(default="", validation_alias="GCP_PROJECT_ID")
    gcp_region: str = Field(default="us-central1", validation_alias="GCP_REGION")
    gcp_service_account_json: str = Field(
        default="", validation_alias="GCP_SERVICE_ACCOUNT_JSON"
    )


class Settings(UnifiedBaseSettings):
    """Application settings.

    Source precedence (highest → lowest): init/CLI > env > .env > yaml > pydantic defaults.
    See docs/superpowers/specs/2026-05-12-unified-config-design.md.
    """

    _yaml_path: ClassVar[str] = "services.orchestration"

    # App info
    app_name: str = "Orchestration Service"
    app_version: str = "0.1.0"
    environment: str = Field(default="development", validation_alias="ENVIRONMENT")
    logstash_host: Optional[str] = Field(default=None, validation_alias="LOGSTASH_HOST")
    logstash_port: int = Field(default=5959, validation_alias="LOGSTASH_PORT")

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

    secret_encryption_key: str = Field(
        default="", validation_alias="SECRET_ENCRYPTION_KEY"
    )

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

    # Worker provisioning. Default points at the GHCR image published by the
    # InferiaAI/inferia-worker repo's docker-publish workflow on v* tags.
    # The org segment "inferiaai" is the GHCR-lowercased form of "InferiaAI".
    worker_image: str = Field(
        default="ghcr.io/inferiaai/inferia-worker",
        validation_alias="INFERIA_WORKER_IMAGE",
    )
    worker_image_tag: str = Field(
        # docker/metadata-action's semver pattern strips the leading "v"
        # from git tags, so the GHCR tag for git tag v0.1.0 is 0.1.0.
        default="0.1.0",
        validation_alias="INFERIA_WORKER_IMAGE_TAG",
    )
    bootstrap_token_ttl_seconds: int = Field(
        default=3600,
        validation_alias="INFERIA_BOOTSTRAP_TOKEN_TTL_SECONDS",
    )
    control_plane_external_url: str = Field(
        default="http://api-gateway:8000",
        validation_alias="INFERIA_CONTROL_PLANE_EXTERNAL_URL",
        description="Public URL workers use to reach /v1/workers/register",
    )
    pulumi_state_dir: str = Field(
        default="/var/lib/inferia/pulumi-state",
        validation_alias="INFERIA_PULUMI_STATE_DIR",
        description="Filesystem path where Pulumi local-backend state is persisted.",
    )
    pulumi_passphrase: str = Field(
        default="",
        validation_alias="INFERIA_PULUMI_PASSPHRASE",
        description="PULUMI_CONFIG_PASSPHRASE — empty disables stack-config secrets.",
    )

    # Model Cache
    model_cache_dir: str = Field(
        default="/var/lib/inferia/models", validation_alias="INFERIA_MODEL_CACHE_DIR"
    )
    model_cache_max_gb: int = Field(
        default=100, validation_alias="INFERIA_MODEL_CACHE_MAX_GB"
    )
    hf_token: str = Field(default="", validation_alias="INFERIA_HF_TOKEN")
    model_mirror_base: str = Field(
        default="", validation_alias="INFERIA_MODEL_MIRROR_BASE"
    )

    # Deployment Log Persistence (Elasticsearch)
    elasticsearch_url: Optional[str] = Field(
        default=None, validation_alias="ELASTICSEARCH_URL"
    )
    deployment_log_buffer_size: int = Field(
        default=10000, validation_alias="DEPLOYMENT_LOG_BUFFER_SIZE"
    )
    deployment_log_flush_interval: int = Field(
        default=10, validation_alias="DEPLOYMENT_LOG_FLUSH_INTERVAL"
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
            "gcp": {
                "project_id": getattr(self, "gcp_project_id", ""),
                "region": getattr(self, "gcp_region", "us-central1"),
                "service_account_json": getattr(self, "gcp_service_account_json", ""),
            },
        }
        return provider_configs.get(provider, {})

    @property
    def is_production(self) -> bool:
        """Check if running in production environment."""
        return getattr(self, "environment", "development") == "production"


settings = Settings()

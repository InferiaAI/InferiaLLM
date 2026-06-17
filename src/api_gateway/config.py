"""
Configuration management for the API Gateway.
Uses Pydantic Settings for environment-based configuration.
"""

from typing import ClassVar, Literal, Optional, Any
import logging
import warnings
from pydantic import Field, BaseModel, field_validator, model_validator
from pydantic_settings import SettingsConfigDict
from common.unified_config import UnifiedBaseSettings

logger = logging.getLogger(__name__)

# --- Nested Provider Configuration Models ---


class AWSConfig(BaseModel):
    access_key_id: Optional[str] = None
    secret_access_key: Optional[str] = None


class GCPConfig(BaseModel):
    project_id: Optional[str] = None
    region: str = "us-central1"
    service_account_json: Optional[str] = None


class AzureConfig(BaseModel):
    subscription_id: Optional[str] = None
    tenant_id: Optional[str] = None
    client_id: Optional[str] = None
    client_secret: Optional[str] = None
    region: str = "eastus"


class IBMConfig(BaseModel):
    api_key: Optional[str] = None
    region: str = "us-south"
    resource_group_id: Optional[str] = None


class CloudConfig(BaseModel):
    aws: AWSConfig = Field(default_factory=AWSConfig)
    gcp: GCPConfig = Field(default_factory=GCPConfig)
    azure: AzureConfig = Field(default_factory=AzureConfig)
    ibm: IBMConfig = Field(default_factory=IBMConfig)


class ChromaConfig(BaseModel):
    api_key: Optional[str] = None
    tenant: Optional[str] = None
    url: Optional[str] = None
    is_local: bool = True
    database: Optional[str] = None


class VectorDBConfig(BaseModel):
    chroma: ChromaConfig = Field(default_factory=ChromaConfig)


class NosanaApiKeyEntry(BaseModel):
    name: str
    key: str
    is_active: bool = True


class NosanaConfig(BaseModel):
    wallet_private_key: Optional[str] = None
    api_keys: list[NosanaApiKeyEntry] = Field(default_factory=list)


class DePINConfig(BaseModel):
    nosana: NosanaConfig = Field(default_factory=NosanaConfig)


class HFTokenEntry(BaseModel):
    name: str
    token: str
    is_active: bool = True


class HuggingFaceConfig(BaseModel):
    token: str = ""  # legacy single token (kept as fallback "default")
    tokens: list[HFTokenEntry] = Field(default_factory=list)


class ProvidersConfig(BaseModel):
    cloud: CloudConfig = Field(default_factory=CloudConfig)
    vectordb: VectorDBConfig = Field(default_factory=VectorDBConfig)
    depin: DePINConfig = Field(default_factory=DePINConfig)
    huggingface: HuggingFaceConfig = Field(default_factory=HuggingFaceConfig)


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
    # Set to "inferiaauth" (SaaS) or "oidc" (enterprise self-hosted OIDC) to
    # delegate authentication to an external IdP.
    # "external" is accepted as a deprecated alias for "inferiaauth".
    # Superadmin login always works locally regardless of this setting.
    auth_provider: Literal["local", "oidc", "inferiaauth"] = Field(
        default="local", validation_alias="AUTH_PROVIDER"
    )

    @field_validator("auth_provider", mode="before")
    @classmethod
    def _coerce_external_alias(cls, v: Any) -> Any:
        """Map legacy AUTH_PROVIDER=external to inferiaauth with a deprecation warning."""
        if v == "external":
            msg = "AUTH_PROVIDER=external is deprecated; use 'inferiaauth'"
            warnings.warn(msg, DeprecationWarning, stacklevel=2)
            logger.warning(msg)
            return "inferiaauth"
        return v
    external_auth_url: Optional[str] = Field(
        default=None,
        validation_alias="EXTERNAL_AUTH_URL",
        description="Base URL of the inferia-auth service (e.g. http://inferia-auth:3000)",
    )
    external_auth_issuer: Optional[str] = Field(
        default=None,
        validation_alias="EXTERNAL_AUTH_ISSUER",
        description="Expected 'iss' claim in inferia-auth-issued JWTs",
    )
    app_namespace: str = Field(
        default="inferiallm",
        validation_alias="APP_NAMESPACE",
        description="Expected 'aud' claim in inferia-auth-issued JWTs",
    )
    oauth_client_id: Optional[str] = Field(
        default=None,
        validation_alias="OAUTH_CLIENT_ID",
        description="OAuth2 client_id registered with inferia-auth for this gateway",
    )
    oauth_redirect_uri: Optional[str] = Field(
        default=None,
        validation_alias="OAUTH_REDIRECT_URI",
        description="OAuth2 redirect_uri pointing back at /auth/callback on this gateway",
    )
    oauth_jwks_cache_ttl_seconds: int = Field(
        default=3600,
        validation_alias="OAUTH_JWKS_CACHE_TTL_SECONDS",
        ge=60,
        le=86400,
        description="JWKS cache lifetime in seconds (60–86400)",
    )
    catalog_admin_token: Optional[str] = Field(
        default=None,
        validation_alias="CATALOG_ADMIN_TOKEN",
        description="Short-lived bearer token authorized to declare InferiaLLM's catalog to InferiaAuth.",
    )
    external_service_id: Optional[str] = Field(
        default=None,
        validation_alias="EXTERNAL_SERVICE_ID",
        description=(
            "Optional explicit UUID of this service as registered in InferiaAuth. "
            "When set, catalog declaration skips the GET /api/v1/services slug-resolve "
            "step and uses this value directly. Mirrors INFERIAGATE_SERVICE_ID on InferiaGate."
        ),
    )
    oidc_groups_claim: str = Field(
        default="groups",
        validation_alias="OIDC_GROUPS_CLAIM",
        description="JWT claim listing the user's IdP groups (oidc mode).",
    )
    oidc_role_map: dict[str, str] = Field(
        default_factory=dict,
        validation_alias="OIDC_ROLE_MAP",
        description="Map of IdP group name -> InferiaLLM catalog role (admin|member|viewer). "
                    "Empty = interim 'authenticated => admin'. Set via JSON, e.g. "
                    '{"llm-admins":"admin","llm-users":"viewer"}.',
    )
    oidc_default_role: str = Field(
        default="viewer",
        validation_alias="OIDC_DEFAULT_ROLE",
        description="Role assigned when no group matches oidc_role_map (oidc mode).",
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
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        populate_by_name=True,
    )

    _PLACEHOLDER_JWT_SECRET = "placeholder-secret-key-at-least-32-chars-long"

    @property
    def is_external_mode(self) -> bool:
        """True when auth is delegated to an external IdP (oidc or inferiaauth)."""
        return self.auth_provider in ("oidc", "inferiaauth")

    @model_validator(mode="after")
    def _validate_external_auth_complete(self):
        """When AUTH_PROVIDER is oidc or inferiaauth, the four external auth fields are mandatory.

        Raises a single ValueError that lists every missing env var so operators
        can fix the config in one pass instead of guess-and-restart.
        """
        if self.is_external_mode:
            required = {
                "EXTERNAL_AUTH_URL": self.external_auth_url,
                "EXTERNAL_AUTH_ISSUER": self.external_auth_issuer,
                "OAUTH_CLIENT_ID": self.oauth_client_id,
                "OAUTH_REDIRECT_URI": self.oauth_redirect_uri,
            }
            missing = [name for name, value in required.items() if not value]
            if missing:
                raise ValueError(
                    f"AUTH_PROVIDER={self.auth_provider} requires: {', '.join(missing)}"
                )
        return self

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


def httpx_verify(settings: "Settings") -> object:
    """Return an httpx ``verify=`` value: CA-bundle path if set, else the verify_ssl bool.

    This is the canonical helper for building httpx clients that respect the
    configured TLS settings. Use it whenever constructing an ``httpx.AsyncClient``
    or ``httpx.Client`` that talks to external HTTPS endpoints (InferiaAuth, OIDC
    IdP, JWKS endpoints, etc.).

    Args:
        settings: The application Settings instance (or any object with
            ``ssl_ca_bundle: Optional[str]`` and ``verify_ssl: bool``).

    Returns:
        The CA-bundle path string when ``ssl_ca_bundle`` is set, otherwise the
        ``verify_ssl`` boolean.
    """
    return settings.ssl_ca_bundle or settings.verify_ssl


# Global settings instance
settings = Settings()

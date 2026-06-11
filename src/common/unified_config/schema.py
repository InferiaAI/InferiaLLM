"""Pydantic models for the unified yaml schema.

Split rule: yaml carries **application behavior** only.
Hosting (host, port, workers, reload), networking (proxy_headers, forwarded_allow_ips),
URLs (service URLs, database URL, Redis URL, dashboard URLs), SSL settings, and
connection credentials all live in env vars — they do NOT belong in this schema.

Services' Settings classes still read those values from env via validation_alias;
removing them from yaml schema does not change env-var behaviour.
"""
from typing import Literal, Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator


# Known placeholder strings that must NOT pass schema validation as real secrets.
# Drawn from .env.sample, deploy/.env.example, and existing per-service defaults
# — including ones long enough (>=32 chars) to slip past the length check.
KNOWN_PLACEHOLDER_SECRETS: frozenset[str] = frozenset(
    {
        "placeholder-secret-key-at-least-32-chars-long",
        "YOUR_32_BYTE_SECRET_KEY_HERE",
        "YOUR_32_BYTE_INTERNAL_API_KEY_HERE",
        "CHANGE_THIS_TO_STRONG_PASSWORD",
        "dev-internal-key-change-in-prod",
        "replace-with-32-byte-base64-encoded-key",
        "32-byte-base64-encoded-key",
    }
)


def _secret_validator(v: Optional[str]) -> Optional[str]:
    if v is None:
        return v
    if len(v) < 32:
        raise ValueError(f"must be at least 32 characters (got {len(v)})")
    if v in KNOWN_PLACEHOLDER_SECRETS:
        raise ValueError("must not be a known placeholder string")
    return v


# ─── security ─────────────────────────────────────────────────────────────
class SecurityConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    jwt_secret_key: Optional[str] = None
    jwt_algorithm: str = "HS256"
    internal_api_key: Optional[str] = None
    secret_encryption_key: Optional[str] = None
    log_encryption_key: Optional[str] = None
    allowed_origins: list[str] = Field(default_factory=list)

    _jwt_v = field_validator("jwt_secret_key")(_secret_validator)
    _iak_v = field_validator("internal_api_key")(_secret_validator)


# ─── services ─────────────────────────────────────────────────────────────
class AuthSection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: Literal["local", "external"] = "local"
    # external_url is a URL → env only; removed from yaml schema


class SuperadminSection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: Optional[str] = None
    password: Optional[str] = None


class RateLimitSection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    requests_per_minute: int = Field(default=10000, ge=0)
    burst_size: int = Field(default=1000, ge=0)
    use_redis: bool = False


class HttpClientSection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    service_timeout_seconds: float = 10.0
    service_connect_timeout_seconds: float = 3.0
    service_max_connections: int = Field(default=500, gt=0)
    service_max_keepalive: int = Field(default=100, gt=0)
    proxy_timeout_seconds: float = 300.0
    proxy_max_connections: int = Field(default=500, gt=0)
    proxy_max_keepalive: int = Field(default=100, gt=0)


class ApiGatewayService(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    # host, port, workers, reload, proxy_headers, forwarded_allow_ips → env only
    # ssl, service_urls, dashboard → env only (URLs and network config)
    default_org_name: str = "Default Organization"
    auth: AuthSection = Field(default_factory=AuthSection)
    superadmin: SuperadminSection = Field(default_factory=SuperadminSection)
    rate_limit: RateLimitSection = Field(default_factory=RateLimitSection)
    http_client: HttpClientSection = Field(default_factory=HttpClientSection)


# ─── inference ────────────────────────────────────────────────────────────

class UpstreamSection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    http_timeout_seconds: float = 60.0
    http_connect_timeout_seconds: float = 10.0
    video_timeout_seconds: float = 300.0
    http_max_connections: int = Field(default=500, gt=0)
    http_max_keepalive_connections: int = Field(default=100, gt=0)
    global_max_in_flight: int = Field(default=0, ge=0)
    per_deployment_max_in_flight: int = Field(default=100, ge=0)
    slot_acquire_timeout_seconds: float = 20.0
    allowed_internal_hosts: str = ""
    max_response_bytes: int = Field(default=52_428_800, gt=0)


class GatewayClientSection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    http_max_connections: int = Field(default=1000, gt=0)
    http_max_keepalive_connections: int = Field(default=100, gt=0)


class ContextCacheSection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ttl: int = Field(default=30, ge=0)
    maxsize: int = Field(default=1000, ge=1)


class QuotaCacheSection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ttl_seconds: float = Field(default=1.0, ge=0)
    maxsize: int = Field(default=10000, ge=1)


class InferenceService(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    # host, port, workers, reload → env only
    # api_gateway_url, external_proxy_url → env only (URLs)
    # ssl, allowed_origins → env only (connection/network config)
    request_timeout: int = Field(default=30, gt=0)
    verify_ssl: bool = True
    upstream: UpstreamSection = Field(default_factory=UpstreamSection)
    gateway_client: GatewayClientSection = Field(default_factory=GatewayClientSection)
    context_cache: ContextCacheSection = Field(default_factory=ContextCacheSection)
    quota_cache: QuotaCacheSection = Field(default_factory=QuotaCacheSection)


# ─── orchestration ────────────────────────────────────────────────────────

class OrchestrationReadinessSection(BaseModel):
    # NOTE: leaf names match orchestration/config.py Settings field names.
    model_config = ConfigDict(extra="forbid")
    default_readiness_timeout: int = Field(default=300, gt=0)
    default_polling_interval: int = Field(default=20, gt=0)
    ephemeral_failure_threshold_minutes: int = Field(default=10, gt=0)


class OrchestrationDeploymentLogsSection(BaseModel):
    # NOTE: leaf names match orchestration/config.py Settings field names.
    model_config = ConfigDict(extra="forbid")
    # elasticsearch_url → env only (URL)
    deployment_log_buffer_size: int = Field(default=10000, gt=0)
    deployment_log_flush_interval: int = Field(default=10, gt=0)


class OrchestrationService(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    # host, http_port, grpc_port → env only
    # api_gateway_database_url, nosana_sidecar_url → env only (URLs)
    readiness: OrchestrationReadinessSection = Field(default_factory=OrchestrationReadinessSection)
    deployment_logs: OrchestrationDeploymentLogsSection = Field(default_factory=OrchestrationDeploymentLogsSection)


class ServicesConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    api_gateway: ApiGatewayService = Field(default_factory=ApiGatewayService)
    inference: InferenceService = Field(default_factory=InferenceService)
    orchestration: OrchestrationService = Field(default_factory=OrchestrationService)


# ─── root ─────────────────────────────────────────────────────────────────
class InferiaConfig(BaseModel):
    """Root of the unified config. Unknown top-level keys are *allowed* but ignored
    (forward-compat); unknown keys inside known sub-trees are rejected (typo guard).

    Split rule: yaml owns application behavior; env owns hosting/port/URL/connection.
    See Section 15 of the design spec for rationale.
    """
    model_config = ConfigDict(extra="ignore")

    version: int = Field(..., description="Schema major; only 1 is supported")
    environment: Literal["development", "staging", "production"] = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"

    # infra (database, redis, logstash) → env only; no longer in yaml schema
    security: SecurityConfig = Field(default_factory=SecurityConfig)
    services: ServicesConfig = Field(default_factory=ServicesConfig)
    # providers are NOT in yaml — manage via `inferiallm providers` CLI or the dashboard

    @field_validator("version")
    @classmethod
    def _check_version(cls, v: int) -> int:
        if v != 1:
            raise ValueError(f"unsupported schema version {v}; this build supports v1")
        return v

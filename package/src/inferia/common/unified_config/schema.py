"""Pydantic models for the unified yaml schema (Sections 6 and 7.4 of the spec).

Forward-compat fields (inference, guardrail, data, orchestration, providers)
accept anything as a placeholder; Phase 2+ will tighten them.
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


# ─── infra ────────────────────────────────────────────────────────────────
class DatabaseConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: Optional[str] = None
    ssl: bool = True


class RedisConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    host: str = "localhost"
    port: int = Field(default=6379, gt=0, le=65535)
    db: str = "0"
    username: Optional[str] = None
    password: Optional[str] = None
    ssl: bool = False


class LogstashConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    host: Optional[str] = None
    port: int = Field(default=5959, gt=0, le=65535)


class InfraConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    redis: RedisConfig = Field(default_factory=RedisConfig)
    logstash: LogstashConfig = Field(default_factory=LogstashConfig)


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
    external_url: Optional[str] = None


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


class SslSection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    verify: bool = True
    ca_bundle: Optional[str] = None


class ServiceUrlsSection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    guardrail: Optional[str] = None
    data: Optional[str] = None
    orchestration: Optional[str] = None
    inference: Optional[str] = None


class ApiGatewayService(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    host: str = "0.0.0.0"
    port: int = Field(default=8000, gt=0, le=65535)
    workers: int = Field(default=1, gt=0)
    reload: bool = False
    proxy_headers: bool = True
    forwarded_allow_ips: Optional[str] = None
    default_org_name: str = "Default Organization"
    auth: AuthSection = Field(default_factory=AuthSection)
    superadmin: SuperadminSection = Field(default_factory=SuperadminSection)
    rate_limit: RateLimitSection = Field(default_factory=RateLimitSection)
    http_client: HttpClientSection = Field(default_factory=HttpClientSection)
    ssl: SslSection = Field(default_factory=SslSection)
    service_urls: ServiceUrlsSection = Field(default_factory=ServiceUrlsSection)


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
    host: str = "0.0.0.0"
    port: int = Field(default=8001, gt=0, le=65535)
    workers: int = Field(default=1, gt=0)
    reload: bool = False
    api_gateway_url: str = "http://localhost:8000"
    external_proxy_url: Optional[str] = None
    request_timeout: int = Field(default=30, gt=0)
    verify_ssl: bool = True
    upstream: UpstreamSection = Field(default_factory=UpstreamSection)
    gateway_client: GatewayClientSection = Field(default_factory=GatewayClientSection)
    context_cache: ContextCacheSection = Field(default_factory=ContextCacheSection)
    quota_cache: QuotaCacheSection = Field(default_factory=QuotaCacheSection)


# ─── guardrail ────────────────────────────────────────────────────────────

class GuardrailControlsSection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enable_guardrails: bool = True
    enable_toxicity: bool = False
    enable_prompt_injection: bool = False
    enable_secrets: bool = False
    enable_code_scanning: bool = False
    enable_sensitive_info: bool = False
    enable_no_refusal: bool = False
    enable_bias: bool = False
    enable_relevance: bool = False


class GuardrailThresholdsSection(BaseModel):
    # NOTE: leaf names match the service Settings field names exactly so the
    # flatten logic in source.py maps them correctly without a prefix collision.
    model_config = ConfigDict(extra="forbid")
    toxicity_threshold: float = Field(default=0.7, ge=0.0, le=1.0)
    prompt_injection_threshold: float = Field(default=0.8, ge=0.0, le=1.0)
    bias_threshold: float = Field(default=0.75, ge=0.0, le=1.0)
    relevance_threshold: float = Field(default=0.5, ge=0.0, le=1.0)


class GuardrailPiiSection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    detection_enabled: bool = True
    anonymize: bool = True
    entity_types: list[str] = Field(default_factory=list)
    max_scan_time_seconds: float = Field(default=5.0, gt=0)


class GuardrailService(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    host: str = "0.0.0.0"
    port: int = Field(default=8002, gt=0, le=65535)
    reload: bool = False
    api_gateway_url: str = "http://localhost:8000"
    default_engine: str = "llm-guard"
    llama_guard_model_id: str = "meta-llama/llama-guard-4-12b"
    banned_substrings: str = ""
    controls: GuardrailControlsSection = Field(default_factory=GuardrailControlsSection)
    thresholds: GuardrailThresholdsSection = Field(default_factory=GuardrailThresholdsSection)
    pii: GuardrailPiiSection = Field(default_factory=GuardrailPiiSection)


# ─── data ─────────────────────────────────────────────────────────────────

class DataService(BaseModel):
    # NOTE: max_ingest_documents and max_document_size_bytes are top-level
    # (no sub-section wrapper) so the flatten produces the exact field names
    # declared in data/config.py Settings.
    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    host: str = "0.0.0.0"
    port: int = Field(default=8003, gt=0, le=65535)
    reload: bool = False
    api_gateway_url: str = "http://localhost:8000"
    redis_url: str = "redis://localhost:6379/0"
    max_ingest_documents: int = Field(default=500, gt=0)
    max_document_size_bytes: int = Field(default=1_000_000, gt=0)


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
    elasticsearch_url: Optional[str] = None
    deployment_log_buffer_size: int = Field(default=10000, gt=0)
    deployment_log_flush_interval: int = Field(default=10, gt=0)


class OrchestrationService(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    host: str = "0.0.0.0"
    http_port: int = Field(default=8080, gt=0, le=65535)
    grpc_port: int = Field(default=50051, gt=0, le=65535)
    nosana_sidecar_url: str = "http://localhost:3000"
    readiness: OrchestrationReadinessSection = Field(default_factory=OrchestrationReadinessSection)
    deployment_logs: OrchestrationDeploymentLogsSection = Field(default_factory=OrchestrationDeploymentLogsSection)


class ServicesConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    api_gateway: ApiGatewayService = Field(default_factory=ApiGatewayService)
    inference: InferenceService = Field(default_factory=InferenceService)
    guardrail: GuardrailService = Field(default_factory=GuardrailService)
    data: DataService = Field(default_factory=DataService)
    orchestration: OrchestrationService = Field(default_factory=OrchestrationService)


# ─── providers (Phase 2 will tighten; for now accept-all) ─────────────────
class ProvidersConfig(BaseModel):
    model_config = ConfigDict(extra="allow")


# ─── root ─────────────────────────────────────────────────────────────────
class InferiaConfig(BaseModel):
    """Root of the unified config. Unknown top-level keys are *allowed* but ignored
    (forward-compat); unknown keys inside known sub-trees are rejected (typo guard)."""
    model_config = ConfigDict(extra="ignore")

    version: int = Field(..., description="Schema major; only 1 is supported")
    environment: Literal["development", "staging", "production"] = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"

    infra: InfraConfig = Field(default_factory=InfraConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)
    services: ServicesConfig = Field(default_factory=ServicesConfig)
    providers: ProvidersConfig = Field(default_factory=ProvidersConfig)

    @field_validator("version")
    @classmethod
    def _check_version(cls, v: int) -> int:
        if v != 1:
            raise ValueError(f"unsupported schema version {v}; this build supports v1")
        return v

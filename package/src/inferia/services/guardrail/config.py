"""
Guardrail Service Configuration.
"""

import logging
from typing import List, Optional
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    """Guardrail Service Settings."""

    # Application Settings
    app_name: str = "InferiaLLM Guardrail Service"
    app_version: str = "0.1.0"
    environment: str = "development"

    # Server Settings
    host: str = "0.0.0.0"
    port: int = 8002
    reload: bool = False
    log_level: str = "INFO"

    # CORS Settings
    allowed_origins: str = Field(
        default="http://localhost:3000,http://localhost:3001,http://localhost:5173,http://localhost:8001",
        validation_alias="ALLOWED_ORIGINS",
    )

    # Global Controls
    enable_guardrails: bool = True

    # Granular Controls
    enable_toxicity: bool = False
    enable_prompt_injection: bool = False
    enable_secrets: bool = False
    enable_code_scanning: bool = False
    enable_sensitive_info: bool = False
    enable_no_refusal: bool = False
    enable_bias: bool = False
    enable_relevance: bool = False

    # Thresholds
    toxicity_threshold: float = 0.7
    prompt_injection_threshold: float = 0.8
    bias_threshold: float = 0.75
    relevance_threshold: float = 0.5

    # PII
    pii_detection_enabled: bool = True
    pii_anonymize: bool = True
    pii_entity_types: List[str] = []
    max_scan_time_seconds: float = 5.0

    # Banned content
    banned_substrings: str = ""

    # Providers
    default_guardrail_engine: str = "llm-guard"

    # Keys (Loaded from ENV)
    groq_api_key: Optional[str] = None
    lakera_api_key: Optional[str] = None
    llama_guard_model_id: str = "meta-llama/llama-guard-4-12b"

    # Control Plane Connection
    api_gateway_url: str = Field(
        default="http://localhost:8000", validation_alias="API_GATEWAY_URL"
    )
    internal_api_key: str = Field(default="", validation_alias="INTERNAL_API_KEY")

    @property
    def is_development(self) -> bool:
        return self.environment == "development"

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    def get_banned_substrings_list(self) -> List[str]:
        if not self.banned_substrings:
            return []
        return [s.strip() for s in self.banned_substrings.split(",") if s.strip()]

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


settings = Settings()

# Alias for backward compatibility
guardrail_settings = settings

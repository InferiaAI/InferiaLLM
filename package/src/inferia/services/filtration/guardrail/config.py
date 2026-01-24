"""
Guardrail configuration.
"""

import logging
from typing import List, Optional, Any
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class GuardrailSettings(BaseSettings):
    """Settings for guardrail engine."""
    
    # Global
    enable_guardrails: bool = True
    # Startup Control (Granular Loading)
    enable_toxicity: bool = False
    enable_prompt_injection: bool = False
    enable_secrets: bool = False # Replaces detect_secrets
    enable_code_scanning: bool = False # Replaces detect_code_injection
    enable_sensitive_info: bool = False
    enable_no_refusal: bool = False
    enable_bias: bool = False # Replaces check_bias
    enable_relevance: bool = False # Replaces check_relevance

    # Thresholds
    toxicity_threshold: float = 0.7
    prompt_injection_threshold: float = 0.8
    bias_threshold: float = 0.75
    relevance_threshold: float = 0.5
    
    # PII
    pii_detection_enabled: bool = True
    pii_anonymize: bool = True
    pii_entity_types: List[str] = []  # Empty list = All defaults
    max_scan_time_seconds: float = 5.0
    
    # Banned content
    banned_substrings: str = ""  # Comma-separated list
    
    # Llama Guard (Groq)
    groq_api_key: Optional[str] = None
    lakera_api_key: Optional[str] = None
    default_guardrail_engine: str = "llm-guard" # Options: "llm-guard", "llama-guard"
    llama_guard_model_id: str = "meta-llama/llama-guard-4-12b"
    
    model_config = SettingsConfigDict(
        env_file=str(Path(__file__).parent.parent / ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        env_prefix="GUARDRAIL_"
    )
    
    def refresh_from_main_settings(self) -> None:
        """Hydrate keys from main application config."""
        try:
            from config import settings
            
            # We always prefer main settings if they are present and non-empty
            # This ensures dynamic updates via API are reflected here
            groq_key = settings.providers.guardrails.groq.api_key
            if groq_key is not None:
                if groq_key:
                    logger.info(f"[GuardrailSettings] Hydrating Groq key from config: {groq_key[:6]}...")
                self.groq_api_key = groq_key
            else:
                logger.debug("[GuardrailSettings] No Groq key found in main settings")
                
            lakera_key = settings.providers.guardrails.lakera.api_key
            if lakera_key is not None:
                if lakera_key:
                    logger.info(f"[GuardrailSettings] Hydrating Lakera key from config: {lakera_key[:6]}...")
                self.lakera_api_key = lakera_key
        except ImportError:
            logger.error("[GuardrailSettings] Failed to import config.settings for hydration")
            pass

    def model_post_init(self, __context: Any) -> None:
        """Hydrate keys from main application config if missing."""
        self.refresh_from_main_settings()
    
    def get_banned_substrings_list(self) -> List[str]:
        """Parse banned substrings from comma-separated string."""
        if not self.banned_substrings:
            return []
        return [s.strip() for s in self.banned_substrings.split(",") if s.strip()]


guardrail_settings = GuardrailSettings()

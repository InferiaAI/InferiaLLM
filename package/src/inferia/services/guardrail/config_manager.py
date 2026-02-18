import logging
from typing import Dict, Any
from inferia.services.guardrail.config import guardrail_settings
from inferia.common.config_manager import HTTPConfigManager, update_pydantic_model

logger = logging.getLogger(__name__)


class GuardrailConfigManager(HTTPConfigManager):
    """
    Polls the Filtration Service for provider configuration.
    Updates local guardrail_settings.
    """

    _instance = None

    def __init__(self):
        super().__init__(
            gateway_url=guardrail_settings.api_gateway_url,
            api_key=guardrail_settings.internal_api_key,
            update_callback=self._update_settings,
            poll_interval=15,
        )

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = GuardrailConfigManager()
        return cls._instance

    def _update_settings(self, providers: Dict[str, Any]):
        """Update local settings from gateway data."""
        guardrails = providers.get("guardrails", {})
        if not guardrails:
            return

        # Map nested JSON to flat settings model
        # 1. Groq
        groq_key = guardrails.get("groq", {}).get("api_key")
        if groq_key:
            guardrail_settings.groq_api_key = groq_key

        # 2. Lakera
        lakera_key = guardrails.get("lakera", {}).get("api_key")
        if lakera_key:
            guardrail_settings.lakera_api_key = lakera_key

        # 3. Llama Guard
        llama_model = guardrails.get("llama_guard", {}).get("model_id")
        if llama_model:
            guardrail_settings.llama_guard_model_id = llama_model

        # Also update global toggle if present
        if "enabled" in guardrails:
            guardrail_settings.enable_guardrails = guardrails["enabled"]

        logger.debug(
            "Guardrail settings updated from Filtration Service (manually mapped)."
        )

    def start_polling(self, gateway_url: str = None, api_key: str = None):
        """Start polling with optional overrides."""
        if gateway_url:
            self.gateway_url = gateway_url
        if api_key:
            self.api_key = api_key
        super().start_polling()


config_manager = GuardrailConfigManager.get_instance()

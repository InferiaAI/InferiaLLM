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
            gateway_url=guardrail_settings.filtration_url,
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
        # Update Pydantic settings model
        # The providers dict might have 'guardrails' key or be flat
        guardrails = providers.get("guardrails", {})
        if guardrails:
            # Check for keys like groq, lakera etc
            update_pydantic_model(guardrail_settings, guardrails)

        logger.debug("Guardrail settings updated from Filtration Service.")

    def start_polling(self, gateway_url: str = None, api_key: str = None):
        """Start polling with optional overrides."""
        if gateway_url:
            self.gateway_url = gateway_url
        if api_key:
            self.api_key = api_key
        super().start_polling()


config_manager = GuardrailConfigManager.get_instance()

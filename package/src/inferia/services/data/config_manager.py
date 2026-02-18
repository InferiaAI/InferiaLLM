import logging
from typing import Dict, Any
from inferia.services.data.config import settings
from inferia.common.config_manager import HTTPConfigManager, update_pydantic_model

logger = logging.getLogger(__name__)


class DataConfigManager(HTTPConfigManager):
    """
    Polls the Filtration Service for provider configuration.
    Updates local data settings (Vectordb, OpenAI for prompt).
    """

    _instance = None

    def __init__(self):
        super().__init__(
            gateway_url=settings.api_gateway_url,
            api_key=settings.internal_api_key,
            update_callback=self._update_settings,
            poll_interval=15,
        )

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = DataConfigManager()
        return cls._instance

    def _update_settings(self, providers: Dict[str, Any]):
        """Update local settings from gateway data."""
        # Update Pydantic settings model
        update_pydantic_model(settings.providers, providers)

        # Re-initialize engine client if Vectordb URL changed
        vectordb = providers.get("vectordb", {})
        chroma = vectordb.get("chroma", {})

        if chroma:
            try:
                from inferia.services.data.engine import data_engine

                data_engine.initialize_client()
            except Exception as e:
                logger.error(f"Failed to re-initialize data engine: {e}")

        logger.debug("Data settings updated from Filtration Service.")

    def start_polling(self, gateway_url: str = None, api_key: str = None):
        """Start polling with optional overrides."""
        if gateway_url:
            self.gateway_url = gateway_url
        if api_key:
            self.api_key = api_key
        super().start_polling()


config_manager = DataConfigManager.get_instance()

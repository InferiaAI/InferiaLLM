import asyncio
import logging
from typing import Dict, Any, Optional, Callable
import httpx
from pydantic import BaseModel

logger = logging.getLogger(__name__)


def update_pydantic_model(model: BaseModel, data: Dict[str, Any]):
    """Recursively update a Pydantic model with data from a dictionary."""
    for key, value in data.items():
        if not hasattr(model, key):
            logger.debug(
                f"Skipping key '{key}' - not found in {model.__class__.__name__}"
            )
            continue

        attr = getattr(model, key)
        if isinstance(value, dict) and isinstance(attr, BaseModel):
            update_pydantic_model(attr, value)
        else:
            try:
                setattr(model, key, value)
                logger.debug(f"Updated {model.__class__.__name__}.{key}")
            except Exception as e:
                logger.warning(
                    f"Failed to set attribute {key} on {model.__class__.__name__}: {e}"
                )


class BaseConfigManager:
    """Base class for background configuration polling."""

    def __init__(self, poll_interval: int = 15):
        self._polling_active = False
        self._task: Optional[asyncio.Task] = None
        self.poll_interval = poll_interval

    async def _poll_loop(self):
        logger.info(f"Starting {self.__class__.__name__} polling loop...")
        while self._polling_active:
            try:
                await self.poll_once()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in {self.__class__.__name__} polling loop: {e}")

            await asyncio.sleep(self.poll_interval)

    async def poll_once(self):
        """Override in subclass to perform a single poll operation."""
        raise NotImplementedError

    def start_polling(self):
        if self._polling_active:
            return
        self._polling_active = True
        self._task = asyncio.create_task(self._poll_loop())

    def stop_polling(self):
        self._polling_active = False
        if self._task:
            self._task.cancel()
            self._task = None


class HTTPConfigManager(BaseConfigManager):
    """Polls configuration from a remote Filtration Gateway service."""

    def __init__(
        self,
        gateway_url: str,
        api_key: str,
        update_callback: Callable[[Dict[str, Any]], None],
        poll_interval: int = 15,
    ):
        super().__init__(poll_interval)
        self.gateway_url = gateway_url
        self.api_key = api_key
        self.update_callback = update_callback

    async def poll_once(self):
        headers = {}
        if self.api_key:
            headers["X-Internal-API-Key"] = self.api_key

        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(
                    f"{self.gateway_url}/internal/config/provider",
                    headers=headers,
                    timeout=5.0,
                )
                if response.status_code == 200:
                    data = response.json()
                    if "providers" in data:
                        self.update_callback(data["providers"])
                else:
                    logger.warning(
                        f"Failed to fetch config from {self.gateway_url}: {response.status_code}"
                    )
            except Exception as e:
                logger.error(f"Error polling config from {self.gateway_url}: {e}")

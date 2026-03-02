import asyncio
import logging
from typing import Dict, Any, List, Optional, Callable, get_args, get_origin
import httpx
from inferia.common.http_client import InternalHttpClient
from pydantic import BaseModel

logger = logging.getLogger(__name__)


def _coerce_field_value(model: BaseModel, key: str, value: Any) -> Any:
    """Coerce a value to match the Pydantic field type, especially lists of models."""
    field_info = model.model_fields.get(key)
    if field_info is None or not isinstance(value, list):
        return value

    annotation = field_info.annotation
    # Unwrap Optional / Union
    origin = get_origin(annotation)
    if origin is list or origin is List:
        args = get_args(annotation)
        if args and isinstance(args[0], type) and issubclass(args[0], BaseModel):
            item_model = args[0]
            return [
                item_model.model_validate(item) if isinstance(item, dict) else item
                for item in value
            ]
    return value


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
                coerced = _coerce_field_value(model, key, value)
                setattr(model, key, coerced)
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
        self._http_client = InternalHttpClient(
            internal_api_key=api_key,
            base_url=gateway_url,
            timeout_seconds=5.0
        )

    async def poll_once(self):
        try:
            response = await self._http_client.get("/internal/config/provider")
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

    def stop_polling(self):
        super().stop_polling()
        # Non-blocking close attempt or just let it be handled by GC/lifecycle
        # For a background poller, we usually close in a dedicated shutdown hook

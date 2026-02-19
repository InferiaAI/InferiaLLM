import httpx
from typing import Optional

from inferia.services.inference.config import settings


class HttpClientManager:
    _client: Optional[httpx.AsyncClient] = None

    @classmethod
    def get_client(cls) -> httpx.AsyncClient:
        if cls._client is None or cls._client.is_closed:
            # Initialize with sensible defaults for high throughput
            cls._client = httpx.AsyncClient(
                timeout=httpx.Timeout(
                    settings.upstream_http_timeout_seconds,
                    connect=settings.upstream_http_connect_timeout_seconds,
                ),
                limits=httpx.Limits(
                    max_keepalive_connections=settings.upstream_http_max_keepalive_connections,
                    max_connections=settings.upstream_http_max_connections,
                ),
                verify=settings.verify_ssl,
            )
        return cls._client

    @classmethod
    async def close_client(cls):
        if cls._client:
            await cls._client.aclose()
            cls._client = None


http_client = HttpClientManager

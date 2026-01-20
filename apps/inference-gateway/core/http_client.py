
import httpx
from typing import Optional

class HttpClientManager:
    _client: Optional[httpx.AsyncClient] = None

    @classmethod
    def get_client(cls) -> httpx.AsyncClient:
        if cls._client is None:
            # Initialize with sensible defaults for high throughput
            cls._client = httpx.AsyncClient(
                timeout=httpx.Timeout(60.0, connect=10.0),
                limits=httpx.Limits(max_keepalive_connections=20, max_connections=100)
            )
        return cls._client

    @classmethod
    async def close_client(cls):
        if cls._client:
            await cls._client.aclose()
            cls._client = None

http_client = HttpClientManager

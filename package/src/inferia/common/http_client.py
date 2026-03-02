"""
Standardized HTTP client for internal microservice communication.
"""

import httpx
import logging
from typing import Optional, Dict, Any
from contextvars import ContextVar

logger = logging.getLogger(__name__)

# Context variables to track across async calls (e.g. for trace IDs)
request_id_ctx: ContextVar[Optional[str]] = ContextVar("request_id", default=None)

class InternalHttpClient:
    """
    HTTP client specifically for service-to-service communication.
    
    Features:
    - Shared AsyncClient instance.
    - Automatic X-Internal-API-Key injection.
    - Request ID forwarding for tracing.
    - Standardized timeout and connection limits.
    """
    
    def __init__(
        self, 
        internal_api_key: str,
        base_url: Optional[str] = None,
        timeout_seconds: float = 30.0,
        max_connections: int = 100,
        max_keepalive: int = 20
    ):
        self._internal_api_key = internal_api_key
        self._base_url = base_url
        self._timeout = httpx.Timeout(timeout_seconds)
        self._limits = httpx.Limits(
            max_connections=max_connections, 
            max_keepalive_connections=max_keepalive
        )
        self._client: Optional[httpx.AsyncClient] = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self._base_url or "",
                timeout=self._timeout,
                limits=self._limits,
                headers=self.get_default_headers()
            )
        return self._client

    def get_default_headers(self) -> Dict[str, str]:
        """Base headers required for all internal requests."""
        headers = {
            "X-Internal-API-Key": self._internal_api_key,
            "Content-Type": "application/json"
        }
        
        # Add Request ID if available in context
        req_id = request_id_ctx.get()
        if req_id:
            headers["X-Request-ID"] = req_id
            
        return headers

    async def request(self, method: str, url: str, **kwargs) -> httpx.Response:
        """Wrapper around httpx.request with automatic header injection."""
        # Ensure latest context is used in headers
        headers = kwargs.pop("headers", {})
        merged_headers = {**self.get_default_headers(), **headers}
        
        try:
            return await self.client.request(method, url, headers=merged_headers, **kwargs)
        except httpx.HTTPError as e:
            logger.error(f"Internal HTTP request failed: {method} {url} - {str(e)}")
            raise

    async def get(self, url: str, **kwargs) -> httpx.Response:
        return await self.request("GET", url, **kwargs)

    async def post(self, url: str, **kwargs) -> httpx.Response:
        return await self.request("POST", url, **kwargs)

    async def put(self, url: str, **kwargs) -> httpx.Response:
        return await self.request("PUT", url, **kwargs)

    async def delete(self, url: str, **kwargs) -> httpx.Response:
        return await self.request("DELETE", url, **kwargs)

    async def close(self):
        if self._client:
            await self._client.aclose()
            self._client = None

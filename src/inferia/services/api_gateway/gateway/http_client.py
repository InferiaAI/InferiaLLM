"""
Shared HTTP client manager for API Gateway downstream calls.
"""

from typing import Optional
import httpx
from inferia.common.http_client import InternalHttpClient
from inferia.services.api_gateway.config import settings


class GatewayHttpClientManager:
    _service_client_wrapper: Optional[InternalHttpClient] = None
    _proxy_client: Optional[httpx.AsyncClient] = None

    @classmethod
    def _get_service_wrapper(cls) -> InternalHttpClient:
        if cls._service_client_wrapper is None:
            cls._service_client_wrapper = InternalHttpClient(
                internal_api_key=settings.internal_api_key,
                timeout_seconds=settings.service_http_timeout_seconds,
                max_connections=settings.service_http_max_connections,
                max_keepalive=settings.service_http_max_keepalive_connections
            )
        return cls._service_client_wrapper

    @classmethod
    def get_service_client(cls) -> httpx.AsyncClient:
        """
        Shared client for internal microservice calls.
        Note: This returns the underlying httpx client for backward compatibility,
        but it's better to use request/get/post methods if possible.
        """
        return cls._get_service_wrapper().client

    @classmethod
    async def post(cls, url: str, **kwargs) -> httpx.Response:
        """Perform an authenticated internal POST request."""
        return await cls._get_service_wrapper().post(url, **kwargs)

    @classmethod
    async def get(cls, url: str, **kwargs) -> httpx.Response:
        """Perform an authenticated internal GET request."""
        return await cls._get_service_wrapper().get(url, **kwargs)

    @classmethod
    def get_proxy_client(cls) -> httpx.AsyncClient:
        """
        Shared client for long-running dashboard proxy calls.
        These are external-facing or long-lived, so they don't use internal auth headers.
        """
        if cls._proxy_client is None or cls._proxy_client.is_closed:
            cls._proxy_client = httpx.AsyncClient(
                timeout=httpx.Timeout(
                    settings.proxy_http_timeout_seconds,
                    connect=settings.service_http_connect_timeout_seconds,
                ),
                limits=httpx.Limits(
                    max_keepalive_connections=settings.proxy_http_max_keepalive_connections,
                    max_connections=settings.proxy_http_max_connections,
                ),
                verify=settings.verify_ssl,
            )
        return cls._proxy_client

    @classmethod
    def get_internal_headers(cls) -> dict:
        """
        Get standard headers for internal service-to-service communication.
        Retained for backward compatibility.
        """
        return cls._get_service_wrapper().get_default_headers()

    @classmethod
    async def close_all(cls):
        if cls._service_client_wrapper:
            await cls._service_client_wrapper.close()
            cls._service_client_wrapper = None
            
        if cls._proxy_client and not cls._proxy_client.is_closed:
            await cls._proxy_client.aclose()
            cls._proxy_client = None


gateway_http_client = GatewayHttpClientManager

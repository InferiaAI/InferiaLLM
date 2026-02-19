"""
Shared HTTP client manager for API Gateway downstream calls.
"""

from typing import Optional

import httpx

from inferia.services.api_gateway.config import settings


class GatewayHttpClientManager:
    _service_client: Optional[httpx.AsyncClient] = None
    _proxy_client: Optional[httpx.AsyncClient] = None

    @classmethod
    def get_service_client(cls) -> httpx.AsyncClient:
        """
        Shared client for internal microservice calls (guardrail/data/orchestration health).
        """
        if cls._service_client is None or cls._service_client.is_closed:
            cls._service_client = httpx.AsyncClient(
                timeout=httpx.Timeout(
                    settings.service_http_timeout_seconds,
                    connect=settings.service_http_connect_timeout_seconds,
                ),
                limits=httpx.Limits(
                    max_keepalive_connections=settings.service_http_max_keepalive_connections,
                    max_connections=settings.service_http_max_connections,
                ),
                verify=settings.verify_ssl,
            )
        return cls._service_client

    @classmethod
    def get_proxy_client(cls) -> httpx.AsyncClient:
        """
        Shared client for long-running dashboard proxy calls.
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
    async def close_all(cls):
        if cls._service_client and not cls._service_client.is_closed:
            await cls._service_client.aclose()
        if cls._proxy_client and not cls._proxy_client.is_closed:
            await cls._proxy_client.aclose()
        cls._service_client = None
        cls._proxy_client = None


gateway_http_client = GatewayHttpClientManager

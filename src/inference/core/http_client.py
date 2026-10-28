import logging

import httpx
from typing import Optional

from inference.config import settings
from inference.core.validators import GuardedBackend

logger = logging.getLogger(__name__)


def _allowed_hosts() -> list[str]:
    from inference.core.service import GatewayService  # service imports this module

    return GatewayService._get_allowed_hosts()


def _guarded_transport(limits: httpx.Limits) -> httpx.AsyncHTTPTransport:
    """A transport whose connections pass the upstream check as they open."""
    transport = httpx.AsyncHTTPTransport(verify=settings.verify_ssl, limits=limits)
    # httpx (pinned <0.28) has no public hook for this; its pool takes one.
    transport._pool._network_backend = GuardedBackend(_allowed_hosts)
    return transport


class HttpClientManager:
    _client: Optional[httpx.AsyncClient] = None
    _guarded: Optional[httpx.AsyncClient] = None

    @classmethod
    def get_client(cls, guarded: bool = False) -> httpx.AsyncClient:
        """The upstream client. *guarded* for endpoints a user typed in: it
        connects only to addresses that pass the upstream check."""
        current = cls._guarded if guarded else cls._client
        if current is None or current.is_closed:
            limits = httpx.Limits(
                max_keepalive_connections=settings.upstream_http_max_keepalive_connections,
                max_connections=settings.upstream_http_max_connections,
            )
            # Initialize with sensible defaults for high throughput
            current = httpx.AsyncClient(
                timeout=httpx.Timeout(
                    settings.upstream_http_timeout_seconds,
                    connect=settings.upstream_http_connect_timeout_seconds,
                ),
                limits=limits,
                verify=settings.verify_ssl,
                transport=_guarded_transport(limits) if guarded else None,
            )
            if guarded:
                cls._guarded = current
            else:
                cls._client = current
            if not settings.verify_ssl:
                logger.warning(
                    "TLS verification disabled for upstream connections. "
                    "Do not use in production."
                )
        return current

    @classmethod
    async def close_client(cls):
        for name in ("_client", "_guarded"):
            client = getattr(cls, name)
            if client:
                await client.aclose()
                setattr(cls, name, None)


http_client = HttpClientManager

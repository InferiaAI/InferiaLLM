"""Upstream proxy security validators.

Validates URLs, headers, and response sizes before forwarding
requests to upstream LLM providers.
"""

import asyncio
import ipaddress
import logging
import socket
from typing import Callable
from urllib.parse import urlparse

import httpcore

logger = logging.getLogger(__name__)

# Names that reach this machine or the cloud metadata service.
_INTERNAL_NAMES = frozenset({
    "localhost", "metadata", "metadata.google.internal", "metadata.goog",
    "instance-data", "instance-data.ec2.internal",
})


def _hostname(url: str) -> str:
    """The URL's host, without the trailing dot that names the same host."""
    return (urlparse(url).hostname or "").rstrip(".")


def _literal_ip(hostname: str):
    """The address *hostname* spells, or None if it is a DNS name.

    inet_aton also reads the forms the resolver accepts but ipaddress does
    not, such as 2852039166 or 0xa9fea9fe for 169.254.169.254.
    """
    try:
        return ipaddress.ip_address(hostname)
    except ValueError:
        pass
    try:
        return ipaddress.ip_address(socket.inet_aton(hostname))
    except OSError:
        return None


def _is_internal(addr) -> bool:
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    return not addr.is_global or addr.is_multicast

_HOP_BY_HOP_HEADERS = frozenset(
    h.lower()
    for h in [
        "Connection",
        "Keep-Alive",
        "Proxy-Authenticate",
        "Proxy-Authorization",
        "TE",
        "Trailers",
        "Transfer-Encoding",
        "Upgrade",
    ]
)


def validate_upstream_url(url: str, allowed_hosts: list[str]) -> str:
    """Validate an upstream URL for scheme, format, and SSRF.

    Args:
        url: The full upstream URL to validate.
        allowed_hosts: Hostnames that bypass private-IP checks.

    Returns:
        The validated URL string (unchanged).

    Raises:
        ValueError: If the URL is invalid or targets a blocked host.
    """
    if "\r" in url or "\n" in url:
        raise ValueError("URL contains illegal characters")

    parsed = urlparse(url)

    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"URL scheme '{parsed.scheme}' not allowed")

    if parsed.username or parsed.password:
        raise ValueError("URL must not contain embedded credentials")

    hostname = _hostname(url)
    if not hostname:
        raise ValueError("URL has no hostname")

    if hostname in allowed_hosts:
        return url

    addr = _literal_ip(hostname)
    internal_name = hostname in _INTERNAL_NAMES or hostname.endswith(".localhost")
    if internal_name or (addr is not None and _is_internal(addr)):
        raise ValueError(f"Upstream host '{hostname}' is not allowed")

    return url


async def check_resolved_host(url: str, allowed_hosts: list[str]) -> None:
    """The check validate_upstream_url makes, on what the name resolves to.

    For user-entered endpoints, so a refusal is a clear 400 before any
    request. GuardedBackend repeats it at connect time, which is the check
    that holds: this lookup and the socket's could get different answers.
    """
    hostname = _hostname(url)
    if hostname in allowed_hosts or _literal_ip(hostname) is not None:
        return
    _refuse_internal(hostname, await _resolve(hostname), allowed_hosts)


def _refuse_internal(hostname: str, addresses: list[str], allowed_hosts: list[str]) -> str:
    """The address to connect to; ValueError if there is none or any is internal.

    An allowed host may be listed by name or by any address it resolves to.
    """
    if not addresses:
        raise ValueError(f"Upstream host '{hostname}' does not resolve")
    for ip in addresses:
        if _is_internal(ipaddress.ip_address(ip)) and ip not in allowed_hosts:
            raise ValueError(f"Upstream host '{hostname}' is not allowed")
    return addresses[0]


class GuardedBackend(httpcore.AsyncNetworkBackend):
    """Opens connections only to addresses the upstream check allows.

    The name is resolved and checked here, and the socket goes to the address
    that passed, so a second DNS answer can not move it (rebinding). The URL
    keeps its hostname: pooled connections and TLS stay per host.
    """

    def __init__(self, allowed_hosts: Callable[[], list[str]]):
        self._allowed_hosts = allowed_hosts
        self._backend = httpcore.AnyIOBackend()

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        hostname = host.rstrip(".")
        allowed = self._allowed_hosts()
        if hostname in allowed:
            address = hostname
        else:
            literal = _literal_ip(hostname)
            addresses = [str(literal)] if literal is not None else await _resolve(hostname)
            try:
                address = _refuse_internal(hostname, addresses, allowed)
            except ValueError as e:
                raise httpcore.ConnectError(str(e)) from None
        return await self._backend.connect_tcp(
            address, port, timeout=timeout, local_address=local_address,
            socket_options=socket_options,
        )

    async def connect_unix_socket(self, path, timeout=None, socket_options=None):
        raise httpcore.ConnectError("Unix sockets are not an upstream")

    async def sleep(self, seconds: float) -> None:
        await self._backend.sleep(seconds)


async def _resolve(hostname: str) -> list[str]:
    """Every address *hostname* resolves to; empty if it does not resolve."""
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(
            hostname, None, type=socket.SOCK_STREAM
        )
    except socket.gaierror:
        return []
    return [info[4][0].split("%", 1)[0] for info in infos]  # drop IPv6 zone ids


def sanitize_headers(headers: dict[str, str]) -> dict[str, str]:
    """Remove headers with CRLF injection attempts.

    Args:
        headers: Request headers dict.

    Returns:
        Cleaned headers dict with dangerous entries removed.
    """
    cleaned = {}
    for key, value in headers.items():
        if "\r" in key or "\n" in key or "\r" in value or "\n" in value:
            logger.warning("Dropped header with CRLF characters: %s", key)
            continue
        cleaned[key] = value
    return cleaned


def strip_hop_by_hop_headers(headers: dict) -> dict:
    """Remove hop-by-hop headers that must not be forwarded by proxies.

    Args:
        headers: Response headers dict.

    Returns:
        Headers dict with hop-by-hop entries removed.
    """
    return {k: v for k, v in headers.items() if k.lower() not in _HOP_BY_HOP_HEADERS}


def check_response_size(content_length: int | None, max_bytes: int) -> None:
    """Check Content-Length header against the configured limit.

    Args:
        content_length: Value from Content-Length header, or None.
        max_bytes: Maximum allowed response size.

    Raises:
        ValueError: If content_length exceeds max_bytes.
    """
    if content_length is not None and content_length > max_bytes:
        raise ValueError(
            f"Response size {content_length} exceeds limit {max_bytes}"
        )

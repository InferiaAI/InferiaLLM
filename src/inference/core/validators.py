"""Upstream proxy security validators.

Validates URLs, headers, and response sizes before forwarding
requests to upstream LLM providers.
"""

import asyncio
import ipaddress
import logging
import socket
from typing import Optional
from urllib.parse import urlparse

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


async def check_resolved_host(url: str, allowed_hosts: list[str]) -> Optional[str]:
    """The check validate_upstream_url makes, on what the name resolves to.

    For user-entered endpoints. An allowed host may be listed by name or by
    any address it resolves to. Returns the address to connect to, or None
    when there is nothing to pin (an allowed or literal host, or no answer).
    """
    hostname = _hostname(url)
    if hostname in allowed_hosts or _literal_ip(hostname) is not None:
        return None
    addresses = await _resolve(hostname)
    for ip in addresses:
        if _is_internal(ipaddress.ip_address(ip)) and ip not in allowed_hosts:
            raise ValueError(f"Upstream host '{hostname}' is not allowed")
    return addresses[0] if addresses else None


def pinned_request(url: str, ip: str) -> tuple[str, dict]:
    """*url* aimed at *ip*, and the request kwargs that keep it the same host.

    Connecting to the checked address stops a name that answers one address
    to the check and another to the client (DNS rebinding). The Host header
    and TLS server name stay the original host, so routing and certificate
    checks are unchanged.
    """
    parsed = urlparse(url)
    host = parsed.hostname
    address = f"[{ip}]" if ":" in ip else ip
    netloc = address if parsed.port is None else f"{address}:{parsed.port}"
    return parsed._replace(netloc=netloc).geturl(), {
        "host": parsed.netloc,
        "extensions": {"sni_hostname": host},
    }


async def _resolve(hostname: str) -> list[str]:
    """Every address *hostname* resolves to; empty if it does not resolve,
    which the request itself then fails on."""
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

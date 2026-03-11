"""Upstream proxy security validators.

Validates URLs, headers, and response sizes before forwarding
requests to upstream LLM providers.
"""

import ipaddress
import logging
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

_PRIVATE_NETWORKS = [
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
]

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

    hostname = parsed.hostname
    if not hostname:
        raise ValueError("URL has no hostname")

    # Check if hostname is a private IP
    try:
        addr = ipaddress.ip_address(hostname)
        is_private = any(addr in net for net in _PRIVATE_NETWORKS)
        if is_private:
            if hostname not in allowed_hosts:
                raise ValueError(f"Upstream host '{hostname}' is not allowed")
    except ValueError as e:
        # Not a valid IP address — it's a DNS name.
        # Re-raise if it's our own "not allowed" error.
        if "not allowed" in str(e) or "illegal" in str(e):
            raise
        # Otherwise it's a hostname, which is fine (no IP check needed).

    return url


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

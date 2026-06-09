"""Async HTTP client that declares InferiaLLM's catalog to InferiaAuth.

Issues a single ``PUT /api/v1/services/inferiallm/catalog`` request to the
central auth service with a short-lived admin token.  The call is best-effort:
on any transient failure (network error, timeout, 4xx/5xx) it returns ``False``
and logs a warning rather than raising, so startup is never blocked by catalog
declaration failures.

Usage (later task wires this into app startup)::

    ok = await declare_catalog(settings.auth_base_url, admin_token)

Return-shape contract:
  * Returns ``True`` on any 2xx response.
  * Returns ``False`` (NO raise) on 4xx / 5xx, network errors, or timeouts.
  * Returns ``False`` immediately (NO request) when inputs fail basic guards.
"""

from __future__ import annotations

import logging
from typing import Optional

import httpx

from inferia.services.api_gateway.rbac.catalog import CATALOG, to_declare_request

logger = logging.getLogger(__name__)

_MAX_ADMIN_TOKEN_LEN = 8192
_DECLARE_TIMEOUT = 30.0
_SERVICE_ID = "inferiallm"


async def declare_catalog(
    base_url: str,
    admin_token: str,
    *,
    client: Optional[httpx.AsyncClient] = None,
) -> bool:
    """PUT InferiaLLM's catalog to InferiaAuth.

    Args:
        base_url: Base URL of the InferiaAuth service (trailing slash stripped).
        admin_token: Short-lived admin bearer token issued by InferiaAuth for
            catalog declaration.
        client: Optional shared ``httpx.AsyncClient``.  When provided it is
            used as-is and will NOT be closed by this function.  When omitted
            a temporary client is created and closed after the request.

    Returns:
        ``True`` on a 2xx response, ``False`` on any failure.
    """
    # ------------------------------------------------------------------
    # Input guards — fail fast without touching the network
    # ------------------------------------------------------------------
    if not base_url:
        logger.warning("declare_catalog: base_url is empty; skipping declaration")
        return False

    if not admin_token:
        logger.warning("declare_catalog: admin_token is empty; skipping declaration")
        return False

    if len(admin_token) > _MAX_ADMIN_TOKEN_LEN:
        logger.warning(
            "declare_catalog: admin_token exceeds %d chars; skipping declaration",
            _MAX_ADMIN_TOKEN_LEN,
        )
        return False

    url = f"{base_url.rstrip('/')}/api/v1/services/{_SERVICE_ID}/catalog"
    body = to_declare_request(CATALOG)
    headers = {"Authorization": f"Bearer {admin_token}"}

    # ------------------------------------------------------------------
    # Issue the request
    # ------------------------------------------------------------------
    _owned_client: Optional[httpx.AsyncClient] = None
    if client is None:
        _owned_client = httpx.AsyncClient(timeout=_DECLARE_TIMEOUT)
        client = _owned_client

    try:
        resp = await client.put(url, json=body, headers=headers, timeout=_DECLARE_TIMEOUT)
    except httpx.TimeoutException as exc:
        logger.warning(
            "declare_catalog: request timed out (%s); catalog not declared",
            type(exc).__name__,
        )
        return False
    except httpx.HTTPError as exc:
        logger.warning(
            "declare_catalog: network error (%s: %s); catalog not declared",
            type(exc).__name__,
            exc,
        )
        return False
    finally:
        if _owned_client is not None:
            await _owned_client.aclose()

    if resp.is_success:
        return True

    logger.warning(
        "declare_catalog: InferiaAuth returned %d; catalog not declared",
        resp.status_code,
    )
    return False

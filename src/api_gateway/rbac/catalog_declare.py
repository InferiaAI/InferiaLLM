"""Async HTTP client that declares InferiaLLM's catalog to InferiaAuth.

Resolves the service's UUID from InferiaAuth (``GET /api/v1/services``) and
then issues a single ``PUT /api/v1/services/{uuid}/catalog`` request with a
short-lived admin token.  The call is best-effort: on any transient failure
(network error, timeout, 4xx/5xx) it returns ``False`` and logs a warning
rather than raising, so startup is never blocked by catalog declaration
failures.

Usage (later task wires this into app startup)::

    ok = await declare_catalog(settings.auth_base_url, admin_token)

    # With explicit service UUID (skip the resolve GET):
    ok = await declare_catalog(
        settings.auth_base_url, admin_token,
        service_id=settings.external_service_id,
    )

Return-shape contract:
  * Returns ``True`` on any 2xx response.
  * Returns ``False`` (NO raise) on 4xx / 5xx, network errors, or timeouts.
  * Returns ``False`` immediately (NO request) when inputs fail basic guards.
  * Returns ``False`` when the service UUID cannot be resolved.
"""

from __future__ import annotations

import logging
from typing import Optional

import httpx

from api_gateway.rbac.catalog import CATALOG, to_declare_request

logger = logging.getLogger(__name__)

_MAX_ADMIN_TOKEN_LEN = 8192
_DECLARE_TIMEOUT = 30.0
_SERVICE_SLUG = "inferiallm"


async def resolve_service_id(
    base_url: str,
    admin_token: str,
    *,
    slug: str = _SERVICE_SLUG,
    client: Optional[httpx.AsyncClient] = None,
    verify: object = True,
) -> Optional[str]:
    """GET {base}/api/v1/services and return the id whose slug matches.

    Returns the UUID string on success, or None on any failure
    (network error, timeout, non-2xx, malformed body, slug not found).
    Never raises. When ``client`` is provided it is used as-is and not closed;
    otherwise a temporary client built with ``verify=verify`` is created and
    closed.

    Args:
        base_url: Base URL of the InferiaAuth service (trailing slash stripped).
        admin_token: Bearer token for the services list endpoint.
        slug: Service slug to match (defaults to ``_SERVICE_SLUG``).
        client: Optional shared ``httpx.AsyncClient``.
        verify: httpx ``verify=`` value (CA-bundle path string or bool).

    Returns:
        UUID string on success, ``None`` on any failure.
    """
    if not base_url:
        logger.warning("resolve_service_id: base_url is empty; cannot resolve")
        return None

    if not admin_token:
        logger.warning("resolve_service_id: admin_token is empty; cannot resolve")
        return None

    url = f"{base_url.rstrip('/')}/api/v1/services"
    headers = {"Authorization": f"Bearer {admin_token}"}

    _owned: Optional[httpx.AsyncClient] = None
    if client is None:
        _owned = httpx.AsyncClient(timeout=_DECLARE_TIMEOUT, verify=verify)
        client = _owned

    try:
        resp = await client.get(url, headers=headers, timeout=_DECLARE_TIMEOUT)
    except httpx.TimeoutException as exc:
        logger.warning(
            "resolve_service_id: request timed out (%s)",
            type(exc).__name__,
        )
        return None
    except httpx.HTTPError as exc:
        logger.warning(
            "resolve_service_id: network error (%s: %s)",
            type(exc).__name__,
            exc,
        )
        return None
    finally:
        if _owned is not None:
            await _owned.aclose()

    if not resp.is_success:
        logger.warning(
            "resolve_service_id: InferiaAuth returned %d; cannot resolve service id",
            resp.status_code,
        )
        return None

    try:
        data = resp.json()
    except ValueError as exc:
        logger.warning("resolve_service_id: response body is not JSON (%s)", exc)
        return None

    if not isinstance(data, dict):
        logger.warning("resolve_service_id: response body is not a JSON object")
        return None

    services = data.get("services")
    if not isinstance(services, list):
        logger.warning("resolve_service_id: 'services' field missing or not a list")
        return None

    for entry in services:
        if isinstance(entry, dict) and entry.get("slug") == slug:
            svc_id = entry.get("id")
            if svc_id and isinstance(svc_id, str):
                return svc_id

    logger.warning(
        "resolve_service_id: service slug %r not found in services list",
        slug,
    )
    return None


async def declare_catalog(
    base_url: str,
    admin_token: str,
    *,
    service_id: Optional[str] = None,
    client: Optional[httpx.AsyncClient] = None,
    verify: object = True,
) -> bool:
    """PUT InferiaLLM's catalog to InferiaAuth.

    Args:
        base_url: Base URL of the InferiaAuth service (trailing slash stripped).
        admin_token: Short-lived admin bearer token issued by InferiaAuth for
            catalog declaration.
        service_id: Optional explicit service UUID. When set, skips the
            ``GET /api/v1/services`` resolution step and uses this value
            directly. Mirrors the ``EXTERNAL_SERVICE_ID`` config field.
        client: Optional shared ``httpx.AsyncClient``.  When provided it is
            used as-is and will NOT be closed by this function.  When omitted
            a temporary client is created and closed after the request.
        verify: httpx ``verify=`` value (CA-bundle path string or bool).
            Passed to the internally-owned client when ``client`` is None.

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

    # ------------------------------------------------------------------
    # Build or reuse the HTTP client
    # ------------------------------------------------------------------
    _owned_client: Optional[httpx.AsyncClient] = None
    if client is None:
        _owned_client = httpx.AsyncClient(timeout=_DECLARE_TIMEOUT, verify=verify)
        client = _owned_client

    try:
        # ------------------------------------------------------------------
        # Resolve the service UUID (skip if an explicit override was given)
        # ------------------------------------------------------------------
        sid = service_id or await resolve_service_id(
            base_url, admin_token, client=client, verify=verify
        )
        if not sid:
            logger.warning(
                "declare_catalog: could not resolve service id; catalog not declared"
            )
            return False

        url = f"{base_url.rstrip('/')}/api/v1/services/{sid}/catalog"
        body = to_declare_request(CATALOG)
        headers = {"Authorization": f"Bearer {admin_token}"}

        # ------------------------------------------------------------------
        # Issue the PUT
        # ------------------------------------------------------------------
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

        if resp.is_success:
            return True

        logger.warning(
            "declare_catalog: InferiaAuth returned %d; catalog not declared",
            resp.status_code,
        )
        return False

    finally:
        if _owned_client is not None:
            await _owned_client.aclose()

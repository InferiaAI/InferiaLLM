"""
HTTP client for delegating authentication to inferia-auth service.
"""

import logging
from typing import Optional

import httpx

from api_gateway.config import settings

logger = logging.getLogger(__name__)

_client: Optional[httpx.AsyncClient] = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            base_url=settings.external_auth_url.rstrip("/") + "/api/v1",
            timeout=10.0,
        )
    return _client


async def close_client():
    global _client
    if _client and not _client.is_closed:
        await _client.aclose()
        _client = None


async def external_login(email: str, password: str) -> Optional[dict]:
    """
    Login via inferia-auth.

    Returns the raw JSON response on success, None on auth failure.
    Raises on network/unexpected errors.
    """
    client = _get_client()
    resp = await client.post(
        "/auth/login",
        json={"email": email, "password": password},
    )
    if resp.status_code == 200:
        return resp.json()
    if resp.status_code in (401, 403):
        return None
    resp.raise_for_status()


async def external_register(email: str, password: str, display_name: str) -> Optional[dict]:
    """
    Register a new user via inferia-auth.

    Returns the raw JSON response on success, None on conflict/validation failure.
    """
    client = _get_client()
    resp = await client.post(
        "/auth/register",
        json={"email": email, "password": password, "display_name": display_name},
    )
    if resp.status_code in (200, 201):
        return resp.json()
    if resp.status_code in (400, 409):
        return None
    resp.raise_for_status()


async def external_introspect(token: str) -> Optional[dict]:
    """
    Validate a token via inferia-auth's introspect endpoint.

    Returns introspect payload on success:
      {"valid": bool, "subject": str, "subject_type": str,
       "subject_id": str, "email": str, "org_ids": [...]}

    Returns None on network error (caller should reject the token).
    """
    client = _get_client()
    try:
        resp = await client.post("/auth/introspect", json={"token": token})
        if resp.status_code == 200:
            return resp.json()
        return None
    except httpx.HTTPError:
        logger.warning("Failed to reach inferia-auth for token introspection")
        return None


async def external_refresh(refresh_token: str) -> Optional[dict]:
    """
    Refresh tokens via inferia-auth.

    Returns {"access_token": ..., "refresh_token": ...} on success.
    """
    client = _get_client()
    resp = await client.post(
        "/auth/refresh",
        json={"refresh_token": refresh_token},
    )
    if resp.status_code == 200:
        return resp.json()
    return None

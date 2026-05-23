"""Async HTTP wrapper around inferia-auth /oauth/token and /oauth/revoke.

Used by the OAuth2 callback handler to exchange codes / refresh tokens
and by the logout flow to revoke refresh tokens. All inputs are length-
capped before the wire so a hostile dashboard / browser can't force the
gateway to send oversized payloads upstream.

Return-shape contract:
  * ``exchange_code`` / ``refresh`` return the parsed JSON token bag on
    2xx, ``None`` on any 4xx (treated as 'auth failed, caller decides'),
    and raise ``OAuthClientError`` on network failure / 5xx.
  * ``revoke`` returns ``True`` on 2xx and 4xx (RFC 7009 says revocation
    of an unknown token is success), ``False`` on network error / 5xx.
"""

from __future__ import annotations

import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)


class OAuthClientError(Exception):
    """Network / upstream-5xx failure talking to inferia-auth."""


_MAX_CODE_LEN = 256
_MAX_VERIFIER_LEN = 256
_MAX_REDIRECT_URI_LEN = 2048
_MAX_REFRESH_LEN = 512
_MAX_REVOKE_TOKEN_LEN = 8192

_ALLOWED_TOKEN_TYPE_HINTS = {"access_token", "refresh_token"}


class OAuthClient:
    """Thin wrapper around inferia-auth's OAuth2 token & revoke endpoints."""

    def __init__(
        self,
        *,
        base_url: str,
        client_id: str,
        timeout: float = 5.0,
        http_client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._client_id = client_id
        self._timeout = timeout
        self._client = http_client

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client

    async def close(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
        self._client = None

    # --- token endpoint -----------------------------------------------------

    async def exchange_code(
        self,
        *,
        code: str,
        code_verifier: str,
        redirect_uri: str,
    ) -> Optional[dict]:
        if not code or len(code) > _MAX_CODE_LEN:
            raise ValueError(f"code must be 1..{_MAX_CODE_LEN} chars")
        if not code_verifier or len(code_verifier) > _MAX_VERIFIER_LEN:
            raise ValueError(f"code_verifier must be 1..{_MAX_VERIFIER_LEN} chars")
        if not redirect_uri or len(redirect_uri) > _MAX_REDIRECT_URI_LEN:
            raise ValueError(
                f"redirect_uri must be 1..{_MAX_REDIRECT_URI_LEN} chars"
            )

        return await self._post_token(
            {
                "grant_type": "authorization_code",
                "code": code,
                "code_verifier": code_verifier,
                "redirect_uri": redirect_uri,
                "client_id": self._client_id,
            }
        )

    async def refresh(self, *, refresh_token: str) -> Optional[dict]:
        if not refresh_token or len(refresh_token) > _MAX_REFRESH_LEN:
            raise ValueError(
                f"refresh_token must be 1..{_MAX_REFRESH_LEN} chars"
            )

        return await self._post_token(
            {
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": self._client_id,
            }
        )

    async def _post_token(self, data: dict) -> Optional[dict]:
        url = f"{self._base_url}/oauth/token"
        try:
            resp = await self._get_client().post(
                url,
                data=data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        except httpx.HTTPError as e:
            raise OAuthClientError(
                f"OAuth token request failed: {type(e).__name__}"
            ) from e

        if resp.status_code >= 500:
            raise OAuthClientError(
                f"OAuth token endpoint returned {resp.status_code}"
            )
        if 400 <= resp.status_code < 500:
            logger.info(
                "OAuth token endpoint returned %s for %s",
                resp.status_code,
                data.get("grant_type"),
            )
            return None

        try:
            return resp.json()
        except ValueError as e:
            raise OAuthClientError(
                f"OAuth token response not JSON: {type(e).__name__}"
            ) from e

    # --- revoke endpoint ----------------------------------------------------

    async def revoke(
        self,
        *,
        token: str,
        token_type_hint: str = "refresh_token",
    ) -> bool:
        if not token or len(token) > _MAX_REVOKE_TOKEN_LEN:
            raise ValueError(
                f"token must be 1..{_MAX_REVOKE_TOKEN_LEN} chars"
            )
        if token_type_hint not in _ALLOWED_TOKEN_TYPE_HINTS:
            raise ValueError(
                f"token_type_hint must be one of {_ALLOWED_TOKEN_TYPE_HINTS}"
            )

        url = f"{self._base_url}/oauth/revoke"
        try:
            resp = await self._get_client().post(
                url,
                data={"token": token, "token_type_hint": token_type_hint},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        except httpx.HTTPError:
            logger.warning("OAuth revoke network error; treating as not revoked")
            return False

        # RFC 7009: 2xx is success; 4xx (incl. 404 for unknown token) is
        # ALSO success since the token is effectively unusable.
        if resp.status_code < 500:
            return True
        logger.warning(
            "OAuth revoke endpoint returned %s; treating as not revoked",
            resp.status_code,
        )
        return False

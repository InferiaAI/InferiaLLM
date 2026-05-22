"""OAuth2 Authorization-Code + PKCE entry/exit endpoints.

  /auth/start    — generates PKCE pair + state, redirects to inferia-auth.
  /auth/callback — verifies state cookie, exchanges code for tokens,
                   redirects to the dashboard with the access token in
                   the URL fragment and the refresh token in an httpOnly
                   cookie.

The dashboard never receives the refresh token directly. The fragment is
deliberately used because (a) it is not sent on subsequent requests
back to the gateway, and (b) it gets dropped by ``history.replaceState``
on the dashboard side as soon as it has been consumed.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import secrets
from typing import Optional
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import RedirectResponse

from inferia.services.api_gateway.config import settings
from inferia.services.api_gateway.rbac.oauth_client import (
    OAuthClient,
    OAuthClientError,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["OAuth"])

# Lifetimes (seconds)
_PKCE_COOKIE_TTL = 600
_REFRESH_COOKIE_TTL = 7 * 24 * 60 * 60  # 7 days

# Length caps reused at the router edge so we fail fast before any
# downstream HTTP call.
_MAX_CODE_LEN = 256
_MAX_STATE_LEN = 1024
_MAX_VERIFIER_LEN = 256

_oauth_client: Optional[OAuthClient] = None


def _get_oauth_client() -> OAuthClient:
    """Lazily build a singleton ``OAuthClient`` from current settings."""
    global _oauth_client
    if _oauth_client is None:
        _oauth_client = OAuthClient(
            base_url=settings.external_auth_url,
            client_id=settings.oauth_client_id,
        )
    return _oauth_client


def _pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _require_external_mode() -> None:
    if (
        settings.auth_provider != "external"
        or not settings.external_auth_url
        or not settings.oauth_client_id
        or not settings.oauth_redirect_uri
    ):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="External SSO is not configured. Use local sign in.",
        )


@router.get("/auth/start")
async def auth_start():
    """Kick off the OAuth2 PKCE flow and redirect to inferia-auth."""
    _require_external_mode()

    verifier = secrets.token_urlsafe(64)
    challenge = _pkce_challenge(verifier)
    state = secrets.token_urlsafe(32)

    params = {
        "response_type": "code",
        "client_id": settings.oauth_client_id,
        "redirect_uri": settings.oauth_redirect_uri,
        "scope": f"openid profile email {settings.app_namespace}",
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    base = settings.external_auth_url.rstrip("/")
    url = f"{base}/oauth/authorize?{urlencode(params)}"

    response = RedirectResponse(url=url, status_code=302)
    response.set_cookie(
        "oauth_state",
        state,
        max_age=_PKCE_COOKIE_TTL,
        httponly=True,
        secure=True,
        samesite="lax",
        path="/",
    )
    response.set_cookie(
        "oauth_verifier",
        verifier,
        max_age=_PKCE_COOKIE_TTL,
        httponly=True,
        secure=True,
        samesite="lax",
        path="/",
    )
    return response


@router.get("/auth/callback")
async def auth_callback(request: Request, code: str, state: str):
    """Exchange a one-time code for tokens, then redirect to dashboard."""
    _require_external_mode()

    # Cheap input gates first, before any cookie inspection or network call.
    if not code or len(code) > _MAX_CODE_LEN:
        raise HTTPException(status_code=400, detail="Invalid code parameter")
    if not state or len(state) > _MAX_STATE_LEN:
        raise HTTPException(status_code=400, detail="Invalid state parameter")

    cookie_state = request.cookies.get("oauth_state")
    if not cookie_state or cookie_state != state:
        raise HTTPException(status_code=400, detail="Invalid state")

    verifier = request.cookies.get("oauth_verifier")
    if not verifier or len(verifier) > _MAX_VERIFIER_LEN:
        raise HTTPException(status_code=400, detail="Missing verifier")

    client = _get_oauth_client()
    try:
        tokens = await client.exchange_code(
            code=code,
            code_verifier=verifier,
            redirect_uri=settings.oauth_redirect_uri,
        )
    except OAuthClientError as e:
        logger.warning("OAuth token exchange failed: %s", e)
        raise HTTPException(
            status_code=502,
            detail="Sign in service is unavailable. Please try again.",
        )

    if tokens is None:
        raise HTTPException(
            status_code=502,
            detail="Sign in service rejected the response.",
        )

    access_token = tokens.get("access_token", "")
    refresh_token = tokens.get("refresh_token", "")

    response = RedirectResponse(
        url=f"/#access_token={access_token}", status_code=302
    )
    if refresh_token:
        response.set_cookie(
            "refresh_token",
            refresh_token,
            max_age=_REFRESH_COOKIE_TTL,
            httponly=True,
            secure=True,
            samesite="lax",
            path="/",
        )
    # Clear the short-lived PKCE cookies.
    response.delete_cookie("oauth_state", path="/")
    response.delete_cookie("oauth_verifier", path="/")
    return response

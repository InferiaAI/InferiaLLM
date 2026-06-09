"""Ed25519 JWT verifier backed by a cached JWKS endpoint.

Implements the inferia-auth side of the OAuth2 SSO integration: pulls
`/.well-known/jwks.json` once per ``cache_ttl`` seconds, then verifies
every incoming bearer token locally with PyJWT.

python-jose 3.5 does NOT support EdDSA, so we use PyJWT[crypto] +
cryptography's Ed25519PublicKey to parse the OKP/Ed25519 JWK manually.
"""

from __future__ import annotations

import base64
import logging
import time
from typing import Optional

import httpx
import jwt as pyjwt
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from jwt import (
    ExpiredSignatureError,
    InvalidAudienceError,
    InvalidIssuerError,
    InvalidSignatureError,
    InvalidTokenError,
    MissingRequiredClaimError,
    PyJWTError,
)

logger = logging.getLogger(__name__)


class JWKSVerifyError(Exception):
    """Raised whenever a token cannot be verified for any reason.

    The internal cause (network, signature, expired, etc.) is folded into
    the message so callers can log/forward; HTTP handlers should map this
    to 401.
    """


_MAX_TOKEN_LEN = 8192
_CLOCK_SKEW_SECONDS = 60


def _b64url_to_bytes(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def _jwk_to_ed25519_public(jwk: dict) -> Ed25519PublicKey:
    """Convert an OKP/Ed25519 JWK dict to a usable PublicKey object."""
    if jwk.get("kty") != "OKP" or jwk.get("crv") != "Ed25519":
        raise JWKSVerifyError("JWK is not an Ed25519 key")
    x = jwk.get("x")
    if not x:
        raise JWKSVerifyError("JWK missing 'x' parameter")
    try:
        raw = _b64url_to_bytes(x)
        return Ed25519PublicKey.from_public_bytes(raw)
    except Exception as e:  # pragma: no cover - defensive
        raise JWKSVerifyError(f"invalid Ed25519 JWK: {type(e).__name__}") from e


class JWKSVerifier:
    """Synchronous verifier with an in-memory JWKS cache.

    Verification is CPU-bound and the JWKS fetch is amortised across many
    tokens, so the public surface is intentionally sync. ``verify`` is an
    async wrapper for callers that prefer await syntax.
    """

    def __init__(
        self,
        *,
        jwks_url: str,
        issuer: str,
        audience: str,
        cache_ttl: int = 3600,
        http_client: Optional[httpx.Client] = None,
        verify: object = True,
    ) -> None:
        self._url = jwks_url
        self._issuer = issuer
        self._audience = audience
        self._cache_ttl = cache_ttl
        self._verify = verify
        self._jwks: Optional[dict] = None
        self._cached_at: float = 0.0
        self._client = http_client or httpx.Client(timeout=5.0, verify=self._verify)

    # --- cache plumbing -----------------------------------------------------

    def _fetch_jwks(self) -> dict:
        try:
            resp = self._client.get(self._url)
            resp.raise_for_status()
            data = resp.json()
        except (httpx.HTTPError, ValueError) as e:
            raise JWKSVerifyError(
                f"JWKS fetch failed: {type(e).__name__}"
            ) from e
        if not isinstance(data, dict) or "keys" not in data:
            raise JWKSVerifyError("JWKS response missing 'keys'")
        return data

    def _get_jwks(self) -> dict:
        if (
            self._jwks is not None
            and (time.time() - self._cached_at) < self._cache_ttl
        ):
            return self._jwks
        self._jwks = self._fetch_jwks()
        self._cached_at = time.time()
        return self._jwks

    def _resolve_key(self, kid: Optional[str]) -> Ed25519PublicKey:
        jwks = self._get_jwks()
        keys = jwks.get("keys") or []
        if kid:
            for jwk in keys:
                if jwk.get("kid") == kid:
                    return _jwk_to_ed25519_public(jwk)
            raise JWKSVerifyError(f"unknown kid: {kid}")
        # No kid in header — fall back to the single key (or first OKP key).
        for jwk in keys:
            if jwk.get("kty") == "OKP" and jwk.get("crv") == "Ed25519":
                return _jwk_to_ed25519_public(jwk)
        raise JWKSVerifyError("no Ed25519 keys in JWKS")

    # --- verification -------------------------------------------------------

    def verify_sync(self, token: str) -> dict:
        if not token or len(token) > _MAX_TOKEN_LEN:
            raise JWKSVerifyError("token length out of range")
        # Pull kid from the unverified header so we know which key to use.
        try:
            header = pyjwt.get_unverified_header(token)
        except PyJWTError as e:
            raise JWKSVerifyError(f"invalid token header: {type(e).__name__}") from e
        if header.get("alg") != "EdDSA":
            raise JWKSVerifyError(
                f"unsupported alg: {header.get('alg')!r}; only EdDSA accepted"
            )
        key = self._resolve_key(header.get("kid"))
        try:
            claims = pyjwt.decode(
                token,
                key=key,
                algorithms=["EdDSA"],
                audience=self._audience,
                issuer=self._issuer,
                leeway=_CLOCK_SKEW_SECONDS,
                options={"require": ["exp", "iat", "iss", "aud", "sub"]},
            )
        except ExpiredSignatureError as e:
            raise JWKSVerifyError("token expired") from e
        except InvalidAudienceError as e:
            raise JWKSVerifyError("invalid audience") from e
        except InvalidIssuerError as e:
            raise JWKSVerifyError("invalid issuer") from e
        except InvalidSignatureError as e:
            raise JWKSVerifyError("invalid signature") from e
        except MissingRequiredClaimError as e:
            raise JWKSVerifyError(f"missing claim: {e}") from e
        except InvalidTokenError as e:
            raise JWKSVerifyError(f"invalid token: {type(e).__name__}") from e
        if claims.get("type") != "access":
            raise JWKSVerifyError("token type must be access")
        return claims

    async def verify(self, token: str) -> dict:
        """Async wrapper around :meth:`verify_sync`.

        Kept thin because the actual work (PyJWT.decode + cached HTTP) is
        synchronous; the wrapper exists so callers in async contexts can
        use ``await`` without juggling executor threads.
        """
        return self.verify_sync(token)

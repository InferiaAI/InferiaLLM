"""Backwards-compatible re-export of the shared JWKS verifier.

The implementation now lives in :mod:`common.jwks_verifier` so that both the
api_gateway (SSO token verification) and the inference data plane (sandbox JWT
verification) can use it without inference importing ``api_gateway.rbac`` —
whose package ``__init__`` pulls in DB models and ``auth_service``.

Importing from ``api_gateway.rbac.jwks_verifier`` keeps working unchanged.
"""

from __future__ import annotations

from common.jwks_verifier import (  # noqa: F401
    JWKSVerifier,
    JWKSVerifyError,
    _b64url_to_bytes,
    _jwk_to_ed25519_public,
)

__all__ = ["JWKSVerifier", "JWKSVerifyError"]

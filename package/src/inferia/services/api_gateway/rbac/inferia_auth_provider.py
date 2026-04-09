"""
Optional authentication provider that delegates token validation to inferia-auth.

Supports two modes:
  1. Local validation using an Ed25519 public key (no HTTP call, preferred)
  2. Remote introspection via inferia-auth's /api/v1/auth/introspect endpoint
"""

import base64
import json
import logging
import time
from typing import Optional

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.exceptions import InvalidSignature

logger = logging.getLogger(__name__)


class InferiaAuthClaims:
    """Claims extracted from an inferia-auth JWT."""

    __slots__ = ("subject", "subject_type", "subject_id", "email", "org_ids")

    def __init__(
        self,
        subject: str,
        subject_type: str,
        subject_id: str,
        email: str,
        org_ids: list[str],
    ):
        self.subject = subject
        self.subject_type = subject_type
        self.subject_id = subject_id
        self.email = email
        self.org_ids = org_ids


def _base64url_decode(s: str) -> bytes:
    padding = 4 - len(s) % 4
    if padding != 4:
        s += "=" * padding
    return base64.urlsafe_b64decode(s)


class InferiaAuthProvider:
    """Validates inferia-auth JWTs either locally or via HTTP introspection."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        public_key_b64: Optional[str] = None,
    ):
        self._base_url = base_url.rstrip("/") if base_url else None
        self._public_key: Optional[Ed25519PublicKey] = None

        if public_key_b64:
            key_bytes = base64.b64decode(public_key_b64)
            self._public_key = Ed25519PublicKey.from_public_bytes(key_bytes)

        if not self._public_key and not self._base_url:
            raise ValueError(
                "InferiaAuthProvider requires INFERIA_AUTH_PUBLIC_KEY "
                "or INFERIA_AUTH_URL (or both)"
            )

    def validate_token_local(self, token: str) -> Optional[InferiaAuthClaims]:
        """Validate JWT locally using the Ed25519 public key."""
        if not self._public_key:
            return None

        try:
            parts = token.split(".")
            if len(parts) != 3:
                return None

            signature = _base64url_decode(parts[2])
            signing_input = f"{parts[0]}.{parts[1]}".encode()

            self._public_key.verify(signature, signing_input)

            payload = json.loads(_base64url_decode(parts[1]))

            if payload.get("exp", 0) < time.time():
                logger.debug("inferia-auth token expired")
                return None

            sub = payload.get("sub", "")
            return InferiaAuthClaims(
                subject=sub,
                subject_type=payload.get("type", ""),
                subject_id=sub.split(":", 1)[1] if ":" in sub else sub,
                email=payload.get("email", ""),
                org_ids=payload.get("org_ids", []),
            )
        except (InvalidSignature, Exception) as exc:
            logger.debug("local JWT validation failed: %s", exc)
            return None

    async def validate_token_remote(self, token: str) -> Optional[InferiaAuthClaims]:
        """Validate JWT via inferia-auth's introspect endpoint."""
        if not self._base_url:
            return None

        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.post(
                    f"{self._base_url}/api/v1/auth/introspect",
                    json={"token": token},
                )
                if resp.status_code != 200:
                    return None

                data = resp.json()
                if not data.get("valid"):
                    return None

                return InferiaAuthClaims(
                    subject=data.get("subject", ""),
                    subject_type=data.get("subject_type", ""),
                    subject_id=data.get("subject_id", ""),
                    email=data.get("email", ""),
                    org_ids=data.get("org_ids", []),
                )
        except Exception as exc:
            logger.error("inferia-auth introspect failed: %s", exc)
            return None

    async def validate_token(self, token: str) -> Optional[InferiaAuthClaims]:
        """Validate token using local key first, falling back to remote introspection."""
        if self._public_key:
            claims = self.validate_token_local(token)
            if claims is not None:
                return claims

        return await self.validate_token_remote(token)

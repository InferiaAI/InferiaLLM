"""Tests for inference sandbox token extraction (extract_api_key).

Covers the external-SSO (oidc/inferiaauth) path — where the bearer is an
EdDSA JWT verified via JWKS — alongside the legacy local HS256 path and the
edge cases (bad alg, bad signature, wrong issuer/audience, length overflow,
prefix-stripping, org_ids fallback, missing Bearer).
"""

import base64
import time

import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from fastapi import HTTPException

from inference.app import extract_api_key
from inference.config import settings

# NB: `from inference import app` would bind the FastAPI instance (the package
# re-exports it), not the module — so the singleton is reset by string path.
_VERIFIER_SINGLETON = "inference.app._sandbox_verifier"

ISS = "https://idp.test"
AUD = "inferiallm"

# One Ed25519 keypair + its JWKS for the whole module.
_PRIV = Ed25519PrivateKey.generate()
_RAW_PUB = _PRIV.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
_X = base64.urlsafe_b64encode(_RAW_PUB).rstrip(b"=").decode()
JWKS = {
    "keys": [
        {"kty": "OKP", "crv": "Ed25519", "kid": "test-key", "x": _X,
         "alg": "EdDSA", "use": "sig"}
    ]
}


def _eddsa(claims: dict, kid: str = "test-key") -> str:
    return pyjwt.encode(claims, _PRIV, algorithm="EdDSA", headers={"kid": kid})


def _access_claims(**over) -> dict:
    now = int(time.time())
    base = {
        "type": "access",
        "iss": ISS,
        "aud": AUD,
        "sub": "user:abc-123",
        "iat": now,
        "exp": now + 3600,
    }
    base.update(over)
    return base


@pytest.fixture
def external_mode(monkeypatch):
    """inferiaauth mode + JWKS injected (no network)."""
    monkeypatch.setattr(settings, "auth_provider", "inferiaauth")
    monkeypatch.setattr(settings, "external_auth_url", ISS)
    monkeypatch.setattr(settings, "external_auth_issuer", ISS)
    monkeypatch.setattr(settings, "app_namespace", AUD)
    monkeypatch.setattr(settings, "oauth_client_id", None)
    monkeypatch.setattr(settings, "ssl_ca_bundle", None)
    monkeypatch.setattr(settings, "verify_ssl", True)
    monkeypatch.setattr(_VERIFIER_SINGLETON, None, raising=False)
    monkeypatch.setattr(
        "common.jwks_verifier.JWKSVerifier._fetch_jwks", lambda self: JWKS
    )
    yield
    monkeypatch.setattr(_VERIFIER_SINGLETON, None, raising=False)


@pytest.fixture
def local_mode(monkeypatch):
    monkeypatch.setattr(settings, "auth_provider", "local")
    monkeypatch.setattr(settings, "jwt_secret_key", "x" * 40)
    monkeypatch.setattr(settings, "jwt_algorithm", "HS256")
    monkeypatch.setattr(_VERIFIER_SINGLETON, None, raising=False)


# --------------------------- external (EdDSA) -------------------------------

def test_external_happy_strips_user_prefix(external_mode):
    token = _eddsa(_access_claims(sub="user:abc-123", org_id="org-9"))
    assert extract_api_key(f"Bearer {token}", sandbox=True) == "sandbox:org-9:abc-123"


def test_external_sub_without_prefix(external_mode):
    token = _eddsa(_access_claims(sub="abc-123", org_id="org-9"))
    assert extract_api_key(f"Bearer {token}", sandbox=True) == "sandbox:org-9:abc-123"


def test_external_org_ids_fallback(external_mode):
    claims = _access_claims(org_ids=["orgA", "orgB"])
    claims.pop("org_id", None)
    token = _eddsa(claims)
    assert extract_api_key(f"Bearer {token}", sandbox=True) == "sandbox:orgA:abc-123"


def test_external_key_has_exactly_three_parts(external_mode):
    # Regression: an un-stripped "user:<uuid>" sub would make 4 colon parts and
    # break the policy engine's len(parts)==3 check.
    token = _eddsa(_access_claims(sub="user:fa73-1356-5052", org_id="o1"))
    assert extract_api_key(f"Bearer {token}", sandbox=True).count(":") == 2


def test_external_invalid_signature(external_mode):
    other = Ed25519PrivateKey.generate()
    token = pyjwt.encode(_access_claims(), other, algorithm="EdDSA",
                         headers={"kid": "test-key"})
    with pytest.raises(HTTPException) as e:
        extract_api_key(f"Bearer {token}", sandbox=True)
    assert e.value.status_code == 401
    assert e.value.detail == "Invalid JWT token for sandbox mode"


def test_external_wrong_alg_hs256_rejected(external_mode):
    # An HS256 token must be rejected in external mode (alg != EdDSA).
    token = pyjwt.encode(_access_claims(), "x" * 40, algorithm="HS256")
    with pytest.raises(HTTPException) as e:
        extract_api_key(f"Bearer {token}", sandbox=True)
    assert e.value.status_code == 401


def test_external_wrong_issuer(external_mode):
    token = _eddsa(_access_claims(iss="https://evil.test"))
    with pytest.raises(HTTPException) as e:
        extract_api_key(f"Bearer {token}", sandbox=True)
    assert e.value.status_code == 401


def test_external_wrong_audience(external_mode):
    token = _eddsa(_access_claims(aud="someone-else"))
    with pytest.raises(HTTPException) as e:
        extract_api_key(f"Bearer {token}", sandbox=True)
    assert e.value.status_code == 401


def test_external_expired(external_mode):
    now = int(time.time())
    token = _eddsa(_access_claims(iat=now - 7200, exp=now - 3600))
    with pytest.raises(HTTPException) as e:
        extract_api_key(f"Bearer {token}", sandbox=True)
    assert e.value.status_code == 401


def test_external_wrong_type_rejected(external_mode):
    token = _eddsa(_access_claims(type="user"))
    with pytest.raises(HTTPException) as e:
        extract_api_key(f"Bearer {token}", sandbox=True)
    assert e.value.status_code == 401


def test_external_length_overflow(external_mode):
    with pytest.raises(HTTPException) as e:
        extract_api_key("Bearer " + "x" * 9000, sandbox=True)
    assert e.value.status_code == 401
    assert e.value.detail == "Invalid JWT token for sandbox mode"


def test_external_garbage_token(external_mode):
    with pytest.raises(HTTPException) as e:
        extract_api_key("Bearer not.a.jwt", sandbox=True)
    assert e.value.status_code == 401


# ----------------------------- local (HS256) --------------------------------

def test_local_happy(local_mode):
    token = pyjwt.encode(
        {"type": "access", "org_id": "o1", "sub": "u1"}, "x" * 40, algorithm="HS256"
    )
    assert extract_api_key(f"Bearer {token}", sandbox=True) == "sandbox:o1:u1"


def test_local_wrong_secret(local_mode):
    token = pyjwt.encode(
        {"type": "access", "org_id": "o1", "sub": "u1"}, "wrong" * 8, algorithm="HS256"
    )
    with pytest.raises(HTTPException) as e:
        extract_api_key(f"Bearer {token}", sandbox=True)
    assert e.value.status_code == 401
    assert e.value.detail == "Invalid JWT token for sandbox mode"


def test_local_wrong_type(local_mode):
    token = pyjwt.encode(
        {"type": "refresh", "org_id": "o1", "sub": "u1"}, "x" * 40, algorithm="HS256"
    )
    with pytest.raises(HTTPException) as e:
        extract_api_key(f"Bearer {token}", sandbox=True)
    assert e.value.status_code == 401
    assert e.value.detail == "Invalid token type for sandbox mode"


# ------------------------------- common -------------------------------------

def test_non_sandbox_returns_raw_token(local_mode):
    assert extract_api_key("Bearer raw-api-key-123", sandbox=False) == "raw-api-key-123"


@pytest.mark.parametrize("hdr", [None, "", "Token abc", "abc"])
def test_missing_or_malformed_bearer(hdr):
    with pytest.raises(HTTPException) as e:
        extract_api_key(hdr, sandbox=True)
    assert e.value.status_code == 401
    assert e.value.detail == "Invalid API Key format"

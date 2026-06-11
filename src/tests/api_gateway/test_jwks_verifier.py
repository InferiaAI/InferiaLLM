"""Tests for JWKSVerifier — Ed25519 JWT verification against a cached JWKS.

Per plan C2: cover 12 cases (happy path + 10 failure modes + cache TTL + skew
+ token length cap before network call).
"""

import base64
import time
from typing import Tuple

import httpx
import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)

from services.api_gateway.rbac.jwks_verifier import (
    JWKSVerifier,
    JWKSVerifyError,
)


def _jwk_from_public(priv: Ed25519PrivateKey, kid: str = "test-key") -> dict:
    raw_pub = priv.public_key().public_bytes(
        encoding=Encoding.Raw, format=PublicFormat.Raw
    )
    x = base64.urlsafe_b64encode(raw_pub).rstrip(b"=").decode()
    return {
        "kty": "OKP",
        "crv": "Ed25519",
        "kid": kid,
        "use": "sig",
        "alg": "EdDSA",
        "x": x,
    }


@pytest.fixture
def keypair() -> Tuple[Ed25519PrivateKey, dict]:
    priv = Ed25519PrivateKey.generate()
    jwks = {"keys": [_jwk_from_public(priv)]}
    return priv, jwks


def _sign_eddsa(priv: Ed25519PrivateKey, claims: dict, headers: dict | None = None) -> str:
    pem = priv.private_bytes(
        Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()
    ).decode()
    h = {"kid": "test-key"}
    if headers:
        h.update(headers)
    return pyjwt.encode(claims, pem, algorithm="EdDSA", headers=h)


def _default_claims(**overrides) -> dict:
    now = int(time.time())
    claims = {
        "iss": "https://auth.local",
        "aud": "inferiallm",
        "sub": "user:01HX",
        "exp": now + 60,
        "iat": now,
        "type": "access",
        "email": "a@b.c",
        "roles": ["admin"],
        "permissions": ["inferiallm:audit:read"],
    }
    claims.update(overrides)
    return claims


def test_verify_happy_path(httpserver, keypair):
    priv, jwks = keypair
    httpserver.expect_request("/.well-known/jwks.json").respond_with_json(jwks)
    token = _sign_eddsa(priv, _default_claims())
    v = JWKSVerifier(
        jwks_url=httpserver.url_for("/.well-known/jwks.json"),
        issuer="https://auth.local",
        audience="inferiallm",
    )
    claims = v.verify_sync(token)
    assert claims["email"] == "a@b.c"
    assert claims["permissions"] == ["inferiallm:audit:read"]
    assert claims["roles"] == ["admin"]
    assert claims["sub"] == "user:01HX"


def test_verify_expired(httpserver, keypair):
    priv, jwks = keypair
    httpserver.expect_request("/.well-known/jwks.json").respond_with_json(jwks)
    # 120 s past, beyond default 60 s skew tolerance
    token = _sign_eddsa(
        priv,
        _default_claims(exp=int(time.time()) - 120, iat=int(time.time()) - 200),
    )
    v = JWKSVerifier(
        jwks_url=httpserver.url_for("/.well-known/jwks.json"),
        issuer="https://auth.local",
        audience="inferiallm",
    )
    with pytest.raises(JWKSVerifyError) as exc:
        v.verify_sync(token)
    assert "expired" in str(exc.value).lower()


def test_verify_wrong_iss(httpserver, keypair):
    priv, jwks = keypair
    httpserver.expect_request("/.well-known/jwks.json").respond_with_json(jwks)
    token = _sign_eddsa(priv, _default_claims(iss="https://evil.local"))
    v = JWKSVerifier(
        jwks_url=httpserver.url_for("/.well-known/jwks.json"),
        issuer="https://auth.local",
        audience="inferiallm",
    )
    with pytest.raises(JWKSVerifyError):
        v.verify_sync(token)


def test_verify_wrong_aud(httpserver, keypair):
    priv, jwks = keypair
    httpserver.expect_request("/.well-known/jwks.json").respond_with_json(jwks)
    token = _sign_eddsa(priv, _default_claims(aud="someoneelse"))
    v = JWKSVerifier(
        jwks_url=httpserver.url_for("/.well-known/jwks.json"),
        issuer="https://auth.local",
        audience="inferiallm",
    )
    with pytest.raises(JWKSVerifyError):
        v.verify_sync(token)


def test_verify_missing_type_claim_rejected(httpserver, keypair):
    priv, jwks = keypair
    httpserver.expect_request("/.well-known/jwks.json").respond_with_json(jwks)
    claims = _default_claims()
    claims.pop("type")
    token = _sign_eddsa(priv, claims)
    v = JWKSVerifier(
        jwks_url=httpserver.url_for("/.well-known/jwks.json"),
        issuer="https://auth.local",
        audience="inferiallm",
    )
    with pytest.raises(JWKSVerifyError) as exc:
        v.verify_sync(token)
    assert "access" in str(exc.value).lower() or "type" in str(exc.value).lower()


def test_verify_type_refresh_rejected(httpserver, keypair):
    priv, jwks = keypair
    httpserver.expect_request("/.well-known/jwks.json").respond_with_json(jwks)
    token = _sign_eddsa(priv, _default_claims(type="refresh"))
    v = JWKSVerifier(
        jwks_url=httpserver.url_for("/.well-known/jwks.json"),
        issuer="https://auth.local",
        audience="inferiallm",
    )
    with pytest.raises(JWKSVerifyError):
        v.verify_sync(token)


def test_verify_wrong_alg_rs256_rejected(httpserver, keypair):
    """An attacker-crafted RS256 token must not be accepted."""
    priv, jwks = keypair
    httpserver.expect_request("/.well-known/jwks.json").respond_with_json(jwks)
    # Encode with HS256 (any non-EdDSA algorithm) using a junk secret.
    token = pyjwt.encode(_default_claims(), "shared-secret", algorithm="HS256",
                         headers={"kid": "test-key"})
    v = JWKSVerifier(
        jwks_url=httpserver.url_for("/.well-known/jwks.json"),
        issuer="https://auth.local",
        audience="inferiallm",
    )
    with pytest.raises(JWKSVerifyError):
        v.verify_sync(token)


def test_verify_signature_tampered(httpserver, keypair):
    priv, jwks = keypair
    httpserver.expect_request("/.well-known/jwks.json").respond_with_json(jwks)
    token = _sign_eddsa(priv, _default_claims())
    # Tamper the payload — re-sign would be impossible without the key, so
    # the signature no longer matches the payload bytes.
    header, payload, sig = token.split(".")
    # Decode, mutate one field, re-encode without re-signing.
    decoded = _b64url_to_bytes_local(payload)
    # Replace the first 'a' (from email "a@b.c") with 'z' to invalidate.
    mutated = decoded.replace(b'"email":"a', b'"email":"z', 1)
    new_payload = (
        base64.urlsafe_b64encode(mutated).rstrip(b"=").decode()
    )
    bad = ".".join([header, new_payload, sig])
    v = JWKSVerifier(
        jwks_url=httpserver.url_for("/.well-known/jwks.json"),
        issuer="https://auth.local",
        audience="inferiallm",
    )
    with pytest.raises(JWKSVerifyError):
        v.verify_sync(bad)


def _b64url_to_bytes_local(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def test_verify_jwks_endpoint_unreachable(keypair):
    priv, _ = keypair
    token = _sign_eddsa(priv, _default_claims())
    # Use a known-bad URL — connection-refused / DNS-fail.
    v = JWKSVerifier(
        jwks_url="http://127.0.0.1:1/.well-known/jwks.json",
        issuer="https://auth.local",
        audience="inferiallm",
        http_client=httpx.Client(timeout=0.5),
    )
    with pytest.raises(JWKSVerifyError):
        v.verify_sync(token)


def test_verify_cache_hits_jwks_once(httpserver, keypair):
    priv, jwks = keypair
    httpserver.expect_request("/.well-known/jwks.json").respond_with_json(jwks)
    v = JWKSVerifier(
        jwks_url=httpserver.url_for("/.well-known/jwks.json"),
        issuer="https://auth.local",
        audience="inferiallm",
        cache_ttl=3600,
    )
    for _ in range(5):
        token = _sign_eddsa(priv, _default_claims())
        v.verify_sync(token)
    # httpserver records every request; expect exactly one JWKS GET.
    matched = [r for r, _ in httpserver.log if r.path == "/.well-known/jwks.json"]
    assert len(matched) == 1


def test_verify_cache_refetches_after_ttl(httpserver, keypair):
    priv, jwks = keypair
    httpserver.expect_request("/.well-known/jwks.json").respond_with_json(jwks)
    v = JWKSVerifier(
        jwks_url=httpserver.url_for("/.well-known/jwks.json"),
        issuer="https://auth.local",
        audience="inferiallm",
        cache_ttl=1,
    )
    v.verify_sync(_sign_eddsa(priv, _default_claims()))
    # Force the cache to expire by rewinding the cached_at marker.
    v._cached_at = time.time() - 10
    v.verify_sync(_sign_eddsa(priv, _default_claims()))
    matched = [r for r, _ in httpserver.log if r.path == "/.well-known/jwks.json"]
    assert len(matched) == 2


def test_verify_clock_skew_tolerated(httpserver, keypair):
    """Token expired 30 s ago should still verify thanks to 60 s leeway."""
    priv, jwks = keypair
    httpserver.expect_request("/.well-known/jwks.json").respond_with_json(jwks)
    now = int(time.time())
    token = _sign_eddsa(priv, _default_claims(exp=now - 30, iat=now - 60))
    v = JWKSVerifier(
        jwks_url=httpserver.url_for("/.well-known/jwks.json"),
        issuer="https://auth.local",
        audience="inferiallm",
    )
    claims = v.verify_sync(token)
    assert claims["sub"] == "user:01HX"


def test_verify_token_too_long_rejected_before_network(httpserver, keypair):
    """No JWKS request should be made when the token is over 8192 chars."""
    # Don't register any expectation — fail loudly if the verifier dials out.
    v = JWKSVerifier(
        jwks_url=httpserver.url_for("/.well-known/jwks.json"),
        issuer="https://auth.local",
        audience="inferiallm",
    )
    with pytest.raises(JWKSVerifyError):
        v.verify_sync("a" * 9000)
    matched = [r for r, _ in httpserver.log if r.path == "/.well-known/jwks.json"]
    assert len(matched) == 0


def test_verify_empty_token_rejected(httpserver, keypair):
    v = JWKSVerifier(
        jwks_url=httpserver.url_for("/.well-known/jwks.json"),
        issuer="https://auth.local",
        audience="inferiallm",
    )
    with pytest.raises(JWKSVerifyError):
        v.verify_sync("")


def test_verify_unknown_kid_rejected(httpserver, keypair):
    priv, jwks = keypair
    httpserver.expect_request("/.well-known/jwks.json").respond_with_json(jwks)
    token = _sign_eddsa(priv, _default_claims(), headers={"kid": "other-key"})
    v = JWKSVerifier(
        jwks_url=httpserver.url_for("/.well-known/jwks.json"),
        issuer="https://auth.local",
        audience="inferiallm",
    )
    with pytest.raises(JWKSVerifyError):
        v.verify_sync(token)


def test_verify_jwks_returns_500(httpserver, keypair):
    priv, _ = keypair
    httpserver.expect_request("/.well-known/jwks.json").respond_with_data(
        "bad", status=500
    )
    v = JWKSVerifier(
        jwks_url=httpserver.url_for("/.well-known/jwks.json"),
        issuer="https://auth.local",
        audience="inferiallm",
    )
    token = _sign_eddsa(priv, _default_claims())
    with pytest.raises(JWKSVerifyError):
        v.verify_sync(token)


@pytest.mark.asyncio
async def test_verify_async_wrapper(httpserver, keypair):
    priv, jwks = keypair
    httpserver.expect_request("/.well-known/jwks.json").respond_with_json(jwks)
    v = JWKSVerifier(
        jwks_url=httpserver.url_for("/.well-known/jwks.json"),
        issuer="https://auth.local",
        audience="inferiallm",
    )
    token = _sign_eddsa(priv, _default_claims())
    claims = await v.verify(token)
    assert claims["sub"] == "user:01HX"


def test_verify_no_kid_falls_back_to_first_key(httpserver, keypair):
    """Token without a kid header should still verify against the single key."""
    priv, jwks = keypair
    httpserver.expect_request("/.well-known/jwks.json").respond_with_json(jwks)
    # Sign WITHOUT a kid — overwrite the default kid header.
    pem = priv.private_bytes(
        Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()
    ).decode()
    token = pyjwt.encode(_default_claims(), pem, algorithm="EdDSA")
    v = JWKSVerifier(
        jwks_url=httpserver.url_for("/.well-known/jwks.json"),
        issuer="https://auth.local",
        audience="inferiallm",
    )
    claims = v.verify_sync(token)
    assert claims["sub"] == "user:01HX"


def test_verify_no_kid_no_ed25519_key_in_jwks_rejected(httpserver, keypair):
    priv, _ = keypair
    # JWKS contains only an RSA key.
    rsa_jwks = {"keys": [{"kty": "RSA", "alg": "RS256", "kid": "rsa-key", "n": "x", "e": "AQAB"}]}
    httpserver.expect_request("/.well-known/jwks.json").respond_with_json(rsa_jwks)
    pem = priv.private_bytes(
        Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()
    ).decode()
    token = pyjwt.encode(_default_claims(), pem, algorithm="EdDSA")
    v = JWKSVerifier(
        jwks_url=httpserver.url_for("/.well-known/jwks.json"),
        issuer="https://auth.local",
        audience="inferiallm",
    )
    with pytest.raises(JWKSVerifyError):
        v.verify_sync(token)


def test_verify_jwk_not_eddsa_rejected(httpserver, keypair):
    priv, _ = keypair
    bad_jwks = {"keys": [{"kty": "RSA", "kid": "test-key", "n": "x", "e": "AQAB"}]}
    httpserver.expect_request("/.well-known/jwks.json").respond_with_json(bad_jwks)
    token = _sign_eddsa(priv, _default_claims())
    v = JWKSVerifier(
        jwks_url=httpserver.url_for("/.well-known/jwks.json"),
        issuer="https://auth.local",
        audience="inferiallm",
    )
    with pytest.raises(JWKSVerifyError) as exc:
        v.verify_sync(token)
    assert "Ed25519" in str(exc.value)


def test_verify_jwk_missing_x_rejected(httpserver, keypair):
    priv, _ = keypair
    bad_jwks = {"keys": [{"kty": "OKP", "crv": "Ed25519", "kid": "test-key"}]}
    httpserver.expect_request("/.well-known/jwks.json").respond_with_json(bad_jwks)
    token = _sign_eddsa(priv, _default_claims())
    v = JWKSVerifier(
        jwks_url=httpserver.url_for("/.well-known/jwks.json"),
        issuer="https://auth.local",
        audience="inferiallm",
    )
    with pytest.raises(JWKSVerifyError) as exc:
        v.verify_sync(token)
    assert "x" in str(exc.value)


def test_verify_jwks_response_missing_keys_field(httpserver, keypair):
    priv, _ = keypair
    httpserver.expect_request("/.well-known/jwks.json").respond_with_json({"foo": "bar"})
    token = _sign_eddsa(priv, _default_claims())
    v = JWKSVerifier(
        jwks_url=httpserver.url_for("/.well-known/jwks.json"),
        issuer="https://auth.local",
        audience="inferiallm",
    )
    with pytest.raises(JWKSVerifyError) as exc:
        v.verify_sync(token)
    assert "keys" in str(exc.value)


def test_verify_malformed_token_header_rejected(httpserver, keypair):
    httpserver.expect_request("/.well-known/jwks.json").respond_with_json(keypair[1])
    v = JWKSVerifier(
        jwks_url=httpserver.url_for("/.well-known/jwks.json"),
        issuer="https://auth.local",
        audience="inferiallm",
    )
    # Not a valid JWT shape: garbage characters that decode-base64-fail.
    with pytest.raises(JWKSVerifyError) as exc:
        v.verify_sync("not.a.token")
    assert "header" in str(exc.value).lower() or "invalid" in str(exc.value).lower()


def test_verify_missing_required_claim_rejected(httpserver, keypair):
    """A token missing 'sub' should be rejected with a missing-claim error."""
    priv, jwks = keypair
    httpserver.expect_request("/.well-known/jwks.json").respond_with_json(jwks)
    claims = _default_claims()
    claims.pop("sub")
    token = _sign_eddsa(priv, claims)
    v = JWKSVerifier(
        jwks_url=httpserver.url_for("/.well-known/jwks.json"),
        issuer="https://auth.local",
        audience="inferiallm",
    )
    with pytest.raises(JWKSVerifyError) as exc:
        v.verify_sync(token)
    assert "claim" in str(exc.value).lower() or "sub" in str(exc.value).lower()


def test_verify_jwks_returns_non_json(httpserver, keypair):
    priv, _ = keypair
    httpserver.expect_request("/.well-known/jwks.json").respond_with_data(
        "<html>not json</html>", content_type="text/html"
    )
    token = _sign_eddsa(priv, _default_claims())
    v = JWKSVerifier(
        jwks_url=httpserver.url_for("/.well-known/jwks.json"),
        issuer="https://auth.local",
        audience="inferiallm",
    )
    with pytest.raises(JWKSVerifyError):
        v.verify_sync(token)


def test_verify_token_exactly_at_max_length_attempted(httpserver, keypair):
    """A token at exactly _MAX_TOKEN_LEN should not be rejected by the length cap.

    It will still fail later (malformed) but it must pass the length gate.
    """
    httpserver.expect_request("/.well-known/jwks.json").respond_with_json(keypair[1])
    v = JWKSVerifier(
        jwks_url=httpserver.url_for("/.well-known/jwks.json"),
        issuer="https://auth.local",
        audience="inferiallm",
    )
    # Construct a string of exactly 8192 chars.
    bogus = "a" * 8192
    with pytest.raises(JWKSVerifyError) as exc:
        v.verify_sync(bogus)
    # If the length gate triggered we'd see "length"; otherwise we get a
    # downstream header decode error.
    assert "length" not in str(exc.value).lower()


# ---------------------------------------------------------------------------
# Bug-2 verify= param — JWKSVerifier stores and threads the TLS setting
# ---------------------------------------------------------------------------


def test_jwks_verifier_stores_verify_default(httpserver, keypair):
    """JWKSVerifier defaults verify=True and stores it on self._verify."""
    v = JWKSVerifier(
        jwks_url=httpserver.url_for("/.well-known/jwks.json"),
        issuer="https://auth.local",
        audience="inferiallm",
    )
    assert v._verify is True


def test_jwks_verifier_stores_verify_ca_bundle(httpserver):
    """JWKSVerifier(verify=<ca_path>) stores the path string on self._verify.

    We inject an http_client so the constructor does not try to load the CA
    file on disk (httpx validates it at construction time).  The _verify
    attribute must still reflect the supplied value.
    """
    injected = httpx.Client(timeout=5.0)
    v = JWKSVerifier(
        jwks_url=httpserver.url_for("/.well-known/jwks.json"),
        issuer="https://auth.local",
        audience="inferiallm",
        http_client=injected,
        verify="/path/to/ca.pem",
    )
    assert v._verify == "/path/to/ca.pem"
    assert v._client is injected  # injected client wins over fallback build


def test_jwks_verifier_stores_verify_false(httpserver):
    """JWKSVerifier(verify=False) stores False on self._verify."""
    v = JWKSVerifier(
        jwks_url=httpserver.url_for("/.well-known/jwks.json"),
        issuer="https://auth.local",
        audience="inferiallm",
        verify=False,
    )
    assert v._verify is False


def test_jwks_verifier_injected_client_wins(httpserver, keypair):
    """When http_client= is injected, it is used regardless of verify=."""
    priv, jwks = keypair
    httpserver.expect_request("/.well-known/jwks.json").respond_with_json(jwks)
    injected = httpx.Client(timeout=5.0)
    v = JWKSVerifier(
        jwks_url=httpserver.url_for("/.well-known/jwks.json"),
        issuer="https://auth.local",
        audience="inferiallm",
        http_client=injected,
        verify="/should/not/matter",
    )
    # The injected client is what's stored (not a freshly-built one).
    assert v._client is injected
    # And it works end-to-end.
    token = _sign_eddsa(priv, _default_claims())
    claims = v.verify_sync(token)
    assert claims["email"] == "a@b.c"

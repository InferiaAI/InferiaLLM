#!/usr/bin/env python3
"""Pure logic for InferiaLLM's ``setup.sh``: secret generation, input validation,
URL derivation, and complete ``.env`` rendering.

Runtime dependencies: **Python 3 standard library only** (so it runs from a bare
checkout). The Fernet key is produced with stdlib base64/urandom — it is a valid
``cryptography.fernet.Fernet`` key without importing ``cryptography`` here (the
test suite verifies validity with the real ``Fernet`` class).

Design notes baked in from prior production incidents:
  * ``DATABASE_URL`` is **bare** ``postgresql://`` — orchestration passes it
    straight to ``asyncpg.create_pool`` which rejects ``postgresql+asyncpg://``.
  * ``SECRET_ENCRYPTION_KEY`` must be a valid Fernet key or the provider seeder
    silently skips.
  * Passwords forbid ``$`` / quotes / backtick / whitespace so they are safe both
    for shell handling and for the ``.env`` that doubles as a compose interpolation
    file; bcrypt truncates at 72 bytes so we cap there.
"""
from __future__ import annotations

import argparse
import base64
import os
import re
import secrets
import sys
from urllib.parse import urlparse

# bcrypt hashes only the first 72 bytes of a password; anything longer is
# silently truncated, so a user typing a long passphrase would authenticate with
# a prefix. Reject it loudly instead.
MAX_PASSWORD_BYTES = 72
MIN_PASSWORD_BYTES = 8

DEFAULT_WORKER_IMAGE_TAG = "0.2.11"
AUTH_MODES = ("local", "inferiaauth", "oidc")

_FORBIDDEN_PW = set("$\"'`")
_PASSWORD_ALPHABET = (
    "abcdefghijklmnopqrstuvwxyz"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "0123456789"
)
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class SetupError(Exception):
    """Raised on any invalid input or unsatisfiable configuration."""


# --------------------------------------------------------------------------- #
# secret generation
# --------------------------------------------------------------------------- #
def gen_hex(n: int = 32) -> str:
    """Return ``n`` random bytes as a hex string (``2*n`` chars)."""
    return secrets.token_hex(n)


def gen_fernet_key() -> str:
    """Return a valid Fernet key: URL-safe base64 of 32 random bytes."""
    return base64.urlsafe_b64encode(os.urandom(32)).decode("ascii")


def gen_password(n: int = 24) -> str:
    """Return a strong, shell/compose-safe alphanumeric password of length ``n``."""
    return "".join(secrets.choice(_PASSWORD_ALPHABET) for _ in range(n))


# --------------------------------------------------------------------------- #
# validation
# --------------------------------------------------------------------------- #
def validate_password(pw: str) -> str:
    nbytes = len(pw.encode("utf-8"))
    if nbytes > MAX_PASSWORD_BYTES:
        raise SetupError(
            f"password is {nbytes} bytes; bcrypt truncates at {MAX_PASSWORD_BYTES} "
            f"bytes — choose a shorter password (<= {MAX_PASSWORD_BYTES} bytes)."
        )
    if nbytes < MIN_PASSWORD_BYTES:
        raise SetupError(
            f"password must be at least {MIN_PASSWORD_BYTES} bytes long."
        )
    if any(c.isspace() for c in pw):
        raise SetupError("password must not contain whitespace.")
    bad = sorted(_FORBIDDEN_PW & set(pw))
    if bad:
        raise SetupError(
            "password must not contain any of $ \" ' ` "
            f"(found: {' '.join(bad)}) — these break the .env / compose interpolation."
        )
    return pw


def validate_email(email: str) -> str:
    if not _EMAIL_RE.match(email or ""):
        raise SetupError(f"invalid email address: {email!r}")
    return email


def parse_origin(url: str) -> str:
    """Validate a public URL and return its origin (``scheme://netloc``)."""
    parsed = urlparse((url or "").strip())
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise SetupError(
            f"invalid public URL {url!r}: expected http(s)://host[:port]"
        )
    return f"{parsed.scheme}://{parsed.netloc}"


# --------------------------------------------------------------------------- #
# URL derivation
# --------------------------------------------------------------------------- #
def derive_urls(origin: str) -> dict:
    """Derive all origin-dependent .env URLs from the public origin."""
    return {
        "ALLOWED_ORIGINS": f"http://localhost:3001,http://127.0.0.1:3001,{origin}",
        "DASHBOARD_API_GATEWAY_URL": f"{origin}/api",
        "DASHBOARD_INFERENCE_URL": f"{origin}/inf",
        "OAUTH_REDIRECT_URI": f"{origin}/auth/callback",
        "INFERIA_CONTROL_PLANE_EXTERNAL_URL": f"{origin}/api",
        "INFERIA_MODEL_MIRROR_BASE": f"{origin}/api",
    }


# --------------------------------------------------------------------------- #
# .env construction
# --------------------------------------------------------------------------- #
def build_env(
    *,
    origin: str,
    app_port: int,
    auth_mode: str,
    email: str,
    password: str,
    pg_password: str,
    secrets_map: dict,
    external_auth_url: str | None = None,
    oauth_client_id: str | None = None,
    worker_image_tag: str = DEFAULT_WORKER_IMAGE_TAG,
    hf_token: str = "",
) -> dict:
    """Build the complete, ordered ``.env`` key/value mapping.

    ``secrets_map`` must provide JWT_SECRET_KEY, INTERNAL_API_KEY,
    SECRET_ENCRYPTION_KEY, LOG_ENCRYPTION_KEY.
    """
    if auth_mode not in AUTH_MODES:
        raise SetupError(f"unknown auth mode {auth_mode!r}; choose one of {AUTH_MODES}")
    validate_email(email)
    validate_password(password)

    is_sso = auth_mode in ("inferiaauth", "oidc")
    if is_sso and not (external_auth_url and oauth_client_id):
        raise SetupError(
            f"auth mode {auth_mode!r} requires --external-auth-url and --oauth-client-id"
        )

    urls = derive_urls(origin)
    ext_auth = external_auth_url if is_sso else "http://inferia-auth:3000"
    ext_issuer = external_auth_url if is_sso else ""

    env: dict[str, str] = {}
    # --- Application Core ---
    env["ENVIRONMENT"] = "production"
    env["LOG_LEVEL"] = "INFO"
    # --- Secrets / superadmin ---
    env["SUPERADMIN_EMAIL"] = email
    env["SUPERADMIN_PASSWORD"] = password
    # --- External SSO / auth ---
    env["AUTH_PROVIDER"] = auth_mode
    env["EXTERNAL_AUTH_URL"] = ext_auth
    env["EXTERNAL_AUTH_ISSUER"] = ext_issuer
    env["APP_NAMESPACE"] = "inferiallm"
    env["OAUTH_CLIENT_ID"] = oauth_client_id if is_sso else ""
    env["OAUTH_REDIRECT_URI"] = urls["OAUTH_REDIRECT_URI"] if is_sso else ""
    env["OAUTH_JWKS_CACHE_TTL_SECONDS"] = "3600"
    env["CATALOG_ADMIN_TOKEN"] = ""
    env["EXTERNAL_SERVICE_ID"] = ""
    env["OIDC_GROUPS_CLAIM"] = "groups"
    env["OIDC_ROLE_MAP"] = "{}"
    env["OIDC_DEFAULT_ROLE"] = "viewer"
    env["VERIFY_SSL"] = "true"
    env["SSL_CA_BUNDLE"] = ""
    env["JWT_SECRET_KEY"] = secrets_map["JWT_SECRET_KEY"]
    env["INTERNAL_API_KEY"] = secrets_map["INTERNAL_API_KEY"]
    env["SECRET_ENCRYPTION_KEY"] = secrets_map["SECRET_ENCRYPTION_KEY"]
    env["LOG_ENCRYPTION_KEY"] = secrets_map["LOG_ENCRYPTION_KEY"]
    env["ENABLE_2FA"] = "false"
    env["INFERIA_SSH_AUTHORIZED_KEYS_FILE"] = "./.ssh/authorized_keys"
    # --- Single-port web ---
    env["APP_PORT"] = str(app_port)
    env["FORWARDED_ALLOW_IPS"] = "*"
    env["ALLOWED_ORIGINS"] = urls["ALLOWED_ORIGINS"]
    env["DASHBOARD_API_GATEWAY_URL"] = urls["DASHBOARD_API_GATEWAY_URL"]
    env["DASHBOARD_INFERENCE_URL"] = urls["DASHBOARD_INFERENCE_URL"]
    env["DASHBOARD_WEB_SOCKET_URL"] = ""
    env["DASHBOARD_SIDECAR_URL"] = ""
    env["INFERIA_CONTROL_PLANE_EXTERNAL_URL"] = urls["INFERIA_CONTROL_PLANE_EXTERNAL_URL"]
    env["INFERIA_MODEL_MIRROR_BASE"] = urls["INFERIA_MODEL_MIRROR_BASE"]
    # --- Internal service ports (loopback) ---
    env["HTTP_PORT"] = "8080"
    env["GRPC_PORT"] = "50051"
    env["DEPIN_SIDECAR_PORT"] = "3000"
    # --- Context / timeouts ---
    env["CONTEXT_CACHE_TTL"] = "60"
    env["CONTEXT_CACHE_MAXSIZE"] = "1000"
    env["UPSTREAM_HTTP_TIMEOUT_SECONDS"] = "600"
    env["UPSTREAM_HTTP_CONNECT_TIMEOUT_SECONDS"] = "20"
    # --- Worker / AWS ---
    env["INFERIA_WORKER_IMAGE"] = "ghcr.io/inferiaai/inferia-worker"
    env["INFERIA_WORKER_IMAGE_TAG"] = worker_image_tag
    env["INFERIA_BAKE_SSM_INSTANCE_PROFILE"] = "inferia-engine-ami-builder"
    env["INFERIA_HF_TOKEN"] = hf_token
    # --- Postgres (bare DSN; password reused across all three) ---
    env["POSTGRES_USER"] = "inferia"
    env["POSTGRES_PASSWORD"] = pg_password
    env["DATABASE_URL"] = f"postgresql://inferia:{pg_password}@postgres:5432/inferia"
    env["DATABASE_SSL"] = "false"
    env["PG_ADMIN_USER"] = "inferia"
    env["PG_ADMIN_PASSWORD"] = pg_password
    env["INFERIA_DB"] = "inferia"
    # --- Redis ---
    env["REDIS_HOST"] = "redis"
    env["REDIS_PORT"] = "6379"
    env["REDIS_DB"] = "0"
    env["REDIS_USERNAME"] = "default"
    env["REDIS_PASSWORD"] = ""
    env["REDIS_SSL"] = "false"
    # --- Model cache ---
    env["INFERIA_MODEL_CACHE_DIR"] = ""
    env["INFERIA_MODEL_CACHE_MAX_GB"] = "100"
    return env


# Secrets preserved verbatim across a --force regeneration: rotating the Fernet
# key would orphan encrypted DB rows; rotating the Postgres password would lock
# out the existing pgdata volume.
_PRESERVE_KEYS = (
    "SECRET_ENCRYPTION_KEY",
    "LOG_ENCRYPTION_KEY",
    "POSTGRES_PASSWORD",
    "PG_ADMIN_PASSWORD",
    "DATABASE_URL",
)


def merge_preserve(existing: dict, fresh: dict) -> dict:
    """Return ``fresh`` with preserve-list keys overridden by ``existing`` when set."""
    out = dict(fresh)
    for k in _PRESERVE_KEYS:
        if existing.get(k):
            out[k] = existing[k]
    return out


# --------------------------------------------------------------------------- #
# render / parse
# --------------------------------------------------------------------------- #
def render_env(env: dict) -> str:
    return "".join(f'{k}="{v}"\n' for k, v in env.items())


def parse_env_text(text: str) -> dict:
    """Parse ``KEY=VALUE`` / ``KEY="VALUE"`` lines, ignoring comments and blanks."""
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        if not key:
            continue
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
            val = val[1:-1]
        out[key] = val
    return out


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _cmd_generate_env(a: argparse.Namespace) -> int:
    try:
        origin = parse_origin(a.public_url)
        password = a.password
        generated = False
        if not password:
            password = gen_password()
            generated = True
        secrets_map = {
            "JWT_SECRET_KEY": gen_hex(32),
            "INTERNAL_API_KEY": gen_hex(32),
            "SECRET_ENCRYPTION_KEY": gen_fernet_key(),
            "LOG_ENCRYPTION_KEY": gen_hex(32),
        }
        env = build_env(
            origin=origin,
            app_port=int(a.app_port),
            auth_mode=a.auth_mode,
            email=a.email,
            password=password,
            pg_password=gen_password(24),
            secrets_map=secrets_map,
            external_auth_url=a.external_auth_url,
            oauth_client_id=a.oauth_client_id,
            worker_image_tag=a.worker_image_tag,
            hf_token=a.hf_token,
        )
        if a.merge and os.path.isfile(a.merge):
            with open(a.merge, "r", encoding="utf-8") as fh:
                env = merge_preserve(parse_env_text(fh.read()), env)
    except (SetupError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if generated:
        # surface the auto-generated password to the orchestrator via stderr
        print(f"GENERATED_SUPERADMIN_PASSWORD={env['SUPERADMIN_PASSWORD']}",
              file=sys.stderr)
    sys.stdout.write(render_env(env))
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="inferia_setup",
                                     description="InferiaLLM .env generator")
    sub = parser.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("generate-env", help="render a complete .env to stdout")
    g.add_argument("--public-url", required=True)
    g.add_argument("--app-port", default="8000")
    g.add_argument("--auth-mode", default="local", choices=AUTH_MODES)
    g.add_argument("--email", required=True)
    g.add_argument("--password", default="")
    g.add_argument("--external-auth-url", default=None)
    g.add_argument("--oauth-client-id", default=None)
    g.add_argument("--worker-image-tag", default=DEFAULT_WORKER_IMAGE_TAG)
    g.add_argument("--hf-token", default="")
    g.add_argument("--merge", default=None, help="existing .env to preserve secrets from")
    g.set_defaults(func=_cmd_generate_env)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

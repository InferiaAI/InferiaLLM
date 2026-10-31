#!/usr/bin/env python3
"""Pure logic for InferiaLLM's ``setup.sh``: secret generation, input validation,
URL derivation, complete ``.env`` rendering, and the host-port preflight.

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
import json
import os
import re
import secrets
import sys
from pathlib import Path
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


def validate_no_env_breaking(value: str, field: str) -> str:
    """Reject ``$ " ' ```` in a value destined for the ``.env``.

    The generated ``.env`` doubles as the docker-compose interpolation source, so
    an unescaped double-quote breaks the quoted line and a ``$`` / backtick is
    re-interpreted by compose. Passwords already enforce this; the other
    user-supplied free-form fields (email, IdP URL, OAuth client id, HF token)
    must too, or a stray character silently corrupts the file.
    """
    bad = sorted(_FORBIDDEN_PW & set(value or ""))
    if bad:
        raise SetupError(
            f"{field} must not contain any of $ \" ' ` "
            f"(found: {' '.join(bad)}) — these break the .env / compose interpolation."
        )
    return value


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
    validate_no_env_breaking(email, "email")
    validate_password(password)
    validate_no_env_breaking(hf_token, "hf-token")

    is_sso = auth_mode in ("inferiaauth", "oidc")
    if is_sso and not (external_auth_url and oauth_client_id):
        raise SetupError(
            f"auth mode {auth_mode!r} requires --external-auth-url and --oauth-client-id"
        )
    if is_sso:
        validate_no_env_breaking(external_auth_url, "external-auth-url")
        validate_no_env_breaking(oauth_client_id, "oauth-client-id")

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
    # Host side of docker-compose.yml's other published ports (see .env.example).
    env["ORCHESTRATION_HOST_PORT"] = "8080"
    env["ENVOY_HOST_PORT"] = "10000"
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
    env["INFERIA_MODEL_EGRESS"] = "true"
    return env


# Secrets preserved verbatim across a --force regeneration: rotating the Fernet
# key would orphan encrypted DB rows; rotating the Postgres password would lock
# out the existing pgdata volume. The host-port overrides are kept too: they
# are only ever set by hand to dodge a port clash on this machine (APP_PORT has
# its own --app-port flag).
_PRESERVE_KEYS = (
    "SECRET_ENCRYPTION_KEY",
    "LOG_ENCRYPTION_KEY",
    "POSTGRES_PASSWORD",
    "PG_ADMIN_PASSWORD",
    "DATABASE_URL",
    "ORCHESTRATION_HOST_PORT",
    "ENVOY_HOST_PORT",
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
# Matches an ``.env`` assignment line so we can rewrite just its value while
# preserving comments / blank lines verbatim. Leading whitespace is tolerated.
_ENV_ASSIGN_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=")


def default_template_path() -> Path:
    """Return the repo-root ``.env.example`` relative to this helper's location.

    The helper lives at ``<repo>/scripts/setup/inferia_setup.py``; the template
    sits at ``<repo>/.env.example`` (``parents[2]``).
    """
    return Path(__file__).resolve().parents[2] / ".env.example"


def render_env(env: dict) -> str:
    """Flat fallback renderer: one quoted ``KEY="VALUE"`` per line, no comments.

    Used when no ``.env.example`` template is available. The template-mirroring
    :func:`render_env_from_template` is preferred so the generated ``.env`` stays
    human-readable and aligned with the example.
    """
    return "".join(f'{k}="{v}"\n' for k, v in env.items())


def render_env_from_template(template: str, env: dict) -> str:
    """Render a ``.env`` that mirrors ``template`` (``.env.example``) line by line.

    * Comment lines (``# ...``) and blank lines are preserved **verbatim** and in
      the same positions, so the generated file reads exactly like the example —
      easy to scan and to edit by hand.
    * Each ``KEY=...`` assignment whose ``KEY`` is in ``env`` is rewritten to
      ``KEY="<value>"`` (always double-quoted). This is safe because every value
      is either machine-generated (hex/base64/derived URLs — no quotes) or a
      user-supplied free-form field that :func:`build_env` has already screened
      with :func:`validate_no_env_breaking` / :func:`validate_password`, so no
      value can contain a ``"`` / ``$`` / backtick that would break the quoted
      line or docker-compose interpolation.
    * A template ``KEY`` not present in ``env`` keeps its example line unchanged.
    * Any ``env`` key **absent** from the template is appended in a clearly
      labelled trailing block, so a value can never be silently dropped if the
      template and :func:`build_env` ever drift out of sync.
    """
    seen: set[str] = set()
    out: list[str] = []
    for raw in template.splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            out.append(raw)
            continue
        m = _ENV_ASSIGN_RE.match(raw)
        if m and m.group(1) in env:
            key = m.group(1)
            out.append(f'{key}="{env[key]}"')
            seen.add(key)
        else:
            # not an assignment we generate (or an unrecognized line) — keep it
            out.append(raw)
    leftover = [k for k in env if k not in seen]
    if leftover:
        out.append("")
        out.append("# --- Additional (generated; not in .env.example) ---")
        out.extend(f'{k}="{env[k]}"' for k in leftover)
    return "\n".join(out) + "\n"


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
# host-port preflight
# --------------------------------------------------------------------------- #
# `docker compose up` fails half-way (some containers created, the app not)
# when a published host port is taken. setup.sh asks compose for the ports it
# will publish and checks them first. Sources:
#   * values: `docker compose config --format json` (fully interpolated, long
#     syntax: published / host_ip / target / protocol);
#   * env var names: `docker compose config --no-interpolate --format json`
#     (same order; short syntax is left as written);
#   * containers: `docker ps` (catches ports docker holds without a host
#     socket, e.g. with the userland proxy disabled, and names the holder).
# Port spec: https://docs.docker.com/reference/compose-file/services/#ports

# Extra advice per variable, for variables whose change has side effects.
_PORT_VAR_NOTES = {
    "ORCHESTRATION_HOST_PORT": (
        "Host-side CLI commands (inferiallm node/worker/models) then need "
        "--orchestration-url http://localhost:<port>, or ORCHESTRATION_URL exported "
        "in your shell. Do not put ORCHESTRATION_URL in .env: the app container "
        "reads .env and must keep reaching orchestration on its own port."
    ),
}

_VAR_RE = re.compile(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)")
_DOCKER_PS_PORT_RE = re.compile(
    r"(?:(\[[^\]]*\]|[^,\s\[]+?):)?(\d+)(?:-(\d+))?->[^/,\s]+/(tcp|udp|sctp)"
)


class PortProbeError(Exception):
    """A host port could not be checked (e.g. the host IP is not on this machine)."""


class PortBinding:
    __slots__ = ("service", "host_ip", "port", "protocol", "target", "env_var")

    def __init__(self, service, host_ip, port, protocol, target, env_var):
        self.service, self.host_ip, self.port = service, host_ip, port
        self.protocol, self.target, self.env_var = protocol, target, env_var

    def where(self) -> str:
        ip = self.host_ip or "all interfaces"
        return f"{ip} -> {self.service}:{self.target}"


class PortConflict:
    __slots__ = ("binding", "reason", "holder")

    def __init__(self, binding, reason, holder=None):
        self.binding, self.reason, self.holder = binding, reason, holder


def _split_port_spec(spec: str) -> list:
    """Split short-syntax ``[ip:]published:target[/proto]`` on ':' outside
    ``${...}`` and ``[...]`` (IPv6)."""
    spec = re.sub(r"/(tcp|udp|sctp)$", "", spec)
    parts, buf, depth = [], [], 0
    for ch in spec:
        if ch in "{[":
            depth += 1
        elif ch in "}]":
            depth -= 1
        if ch == ":" and depth == 0:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    parts.append("".join(buf))
    return parts


def _published_env_var(entry):
    """Name of the env var that sets the published (host) port of a raw
    ``ports:`` entry, or None if it is hard-coded / ephemeral."""
    if isinstance(entry, dict):
        published = entry.get("published")
    else:
        parts = _split_port_spec(str(entry))
        if len(parts) < 2:
            return None
        published = parts[-2]
    m = _VAR_RE.search(str(published)) if published is not None else None
    return m.group(1) if m else None


def _expand_published(value) -> list:
    if value in (None, ""):
        return []
    text = str(value)
    if "-" in text:
        lo, hi = text.split("-", 1)
        return list(range(int(lo), int(hi) + 1))
    return [int(text)]


def compose_port_bindings(config: dict, raw: dict) -> list:
    """Host ports the stack publishes, one PortBinding per (port, entry)."""
    out = []
    raw_services = (raw or {}).get("services") or {}
    for name, svc in (config.get("services") or {}).items():
        ports = (svc or {}).get("ports") or []
        raw_ports = (raw_services.get(name) or {}).get("ports") or []
        aligned = len(raw_ports) == len(ports)
        for i, p in enumerate(ports):
            var = _published_env_var(raw_ports[i]) if aligned else None
            for port in _expand_published(p.get("published")):
                out.append(PortBinding(
                    service=name,
                    host_ip=str(p.get("host_ip") or ""),
                    port=port,
                    protocol=str(p.get("protocol") or "tcp"),
                    target=p.get("target"),
                    env_var=var,
                ))
    return out


def parse_docker_ps(text: str) -> list:
    """Parse ``docker ps --format '{{.Label "com.docker.compose.project"}}\\t{{.Names}}\\t{{.Ports}}'``
    into ``(project, container, host_ip, port, protocol)`` tuples."""
    out = []
    for line in text.splitlines():
        if not line.strip():
            continue
        cols = line.split("\t")
        if len(cols) < 3:
            continue
        project, name, ports = cols[0], cols[1], cols[2]
        for m in _DOCKER_PS_PORT_RE.finditer(ports):
            ip = (m.group(1) or "0.0.0.0").strip("[]")
            lo = int(m.group(2))
            hi = int(m.group(3) or lo)
            for port in range(lo, hi + 1):
                out.append((project, name, ip, port, m.group(4)))
    return out


def _all_interfaces(ip: str) -> bool:
    return ip in ("", "0.0.0.0", "::")


def _ips_overlap(a: str, b: str) -> bool:
    return _all_interfaces(a) or _all_interfaces(b) or a == b


def _probe(family, addr, port, protocol) -> bool:
    import errno
    import socket

    kind = socket.SOCK_DGRAM if protocol == "udp" else socket.SOCK_STREAM
    try:
        s = socket.socket(family, kind)
    except OSError:
        return True  # address family unsupported here (e.g. no IPv6): nothing to clash with
    try:
        if family == socket.AF_INET6:
            s.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
        if sys.platform.startswith("linux"):
            # Mirror the daemon (Go's net.Listen sets SO_REUSEADDR): ignore
            # TIME_WAIT leftovers. On Linux a LISTENing socket still conflicts.
            # Elsewhere (BSD/macOS) SO_REUSEADDR would let a wildcard bind past
            # a specific-address listener, so probe without it there.
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind((addr, port))
        if kind == socket.SOCK_STREAM:
            s.listen(1)
        return True
    except OSError as e:
        if e.errno == errno.EADDRINUSE:
            return False
        if e.errno == errno.EADDRNOTAVAIL:
            raise PortProbeError(f"host IP {addr} is not available on this machine") from e
        if e.errno == errno.EACCES:
            return True  # privileged port: docker binds as root, so not a conflict
        raise PortProbeError(f"cannot check {addr}:{port}: {e}") from e
    finally:
        s.close()


def port_is_free(host_ip: str, port: int, protocol: str = "tcp") -> bool:
    """True if docker could publish ``host_ip:port`` (all interfaces when '')."""
    import ipaddress
    import socket

    if _all_interfaces(host_ip):
        # docker publishes both 0.0.0.0 and [::] for an all-interfaces port.
        return _probe(socket.AF_INET, "0.0.0.0", port, protocol) and _probe(
            socket.AF_INET6, "::", port, protocol)
    family = socket.AF_INET6 if ipaddress.ip_address(host_ip).version == 6 else socket.AF_INET
    return _probe(family, host_ip, port, protocol)


def check_ports(bindings, *, published=(), project="", is_free=port_is_free) -> list:
    """Conflicts that would make `docker compose up` fail to bind."""
    conflicts = []
    seen = []
    for b in bindings:
        dup = next((o for o in seen if o.port == b.port and o.protocol == b.protocol
                    and _ips_overlap(o.host_ip, b.host_ip)), None)
        seen.append(b)
        if dup is not None:
            conflicts.append(PortConflict(
                b, f"is published twice in the stack ({dup.service}:{dup.target} and "
                   f"{b.service}:{b.target})"))
            continue
        holders = [p for p in published
                   if p[3] == b.port and p[4] == b.protocol and _ips_overlap(p[2], b.host_ip)]
        if any(p[0] == project and project for p in holders):
            continue  # this stack already holds it; `compose up` keeps or recreates it
        other = next((p for p in holders if p[0] != project or not project), None)
        if other is not None:
            conflicts.append(PortConflict(b, "is already published", holder=f"container {other[1]}"))
            continue
        try:
            free = is_free(b.host_ip, b.port, b.protocol)
        except PortProbeError as e:
            conflicts.append(PortConflict(b, str(e)))
            continue
        if not free:
            conflicts.append(PortConflict(b, "is already in use"))
    return conflicts


def suggest_free_port(start, host_ip, protocol, *, is_free=port_is_free, reserved=()):
    for port in range(start + 1, min(start + 200, 65535) + 1):
        if port in reserved:
            continue
        try:
            if is_free(host_ip, port, protocol):
                return port
        except PortProbeError:
            return None
    return None


def format_conflicts(conflicts, *, env_file, is_free=port_is_free, reserved=()) -> str:
    lines = []
    for c in conflicts:
        b = c.binding
        who = f" by {c.holder}" if c.holder else ""
        lines.append(f"Host port {b.port}/{b.protocol} ({b.where()}) {c.reason}{who}.")
        if c.holder is None and "use" in c.reason:
            lines.append(f"    Find the process: ss -ltnp 'sport = :{b.port}'  "
                         f"(macOS: lsof -nP -iTCP:{b.port} -sTCP:LISTEN)")
        if b.env_var:
            hint = suggest_free_port(b.port, b.host_ip, b.protocol, is_free=is_free,
                                     reserved=set(reserved))
            eg = f", e.g. {b.env_var}={hint}" if hint else ""
            lines.append(f"    Fix: set {b.env_var} to a free port in {env_file}{eg}, "
                         f"or stop whatever holds {b.port}.")
            note = _PORT_VAR_NOTES.get(b.env_var)
            if note:
                lines.append(f"    Note: {note}")
        else:
            lines.append(f"    Fix: change the '{b.service}' ports: mapping in docker-compose.yml, "
                         f"or stop whatever holds {b.port}.")
    return "\n".join(lines)


def _cmd_check_ports(a: argparse.Namespace) -> int:
    try:
        config = json.loads(Path(a.config).read_text())
        raw = json.loads(Path(a.raw).read_text())
    except (OSError, ValueError) as e:
        sys.stderr.write(f"error: cannot read docker compose config output: {e}\n")
        return 2
    ps_text = Path(a.docker_ps).read_text() if a.docker_ps else ""
    bindings = compose_port_bindings(config, raw)
    conflicts = check_ports(bindings, published=parse_docker_ps(ps_text),
                            project=str(config.get("name") or ""))
    if conflicts:
        sys.stderr.write(format_conflicts(conflicts, env_file=a.env_file,
                                          reserved={b.port for b in bindings}) + "\n")
        return 3
    sys.stdout.write(" ".join(f"{b.port}/{b.protocol}" for b in bindings) + "\n")
    return 0


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

        # Resolve the template to mirror: explicit --template wins, else the
        # repo-root .env.example next to this helper, if present.
        template_path = a.template
        if template_path is None:
            dp = default_template_path()
            if dp.is_file():
                template_path = str(dp)
        template_text = None
        if template_path and os.path.isfile(template_path):
            with open(template_path, "r", encoding="utf-8") as fh:
                template_text = fh.read()
    except (SetupError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if generated:
        # surface the auto-generated password to the orchestrator via stderr
        print(f"GENERATED_SUPERADMIN_PASSWORD={env['SUPERADMIN_PASSWORD']}",
              file=sys.stderr)
    if template_text is not None:
        sys.stdout.write(render_env_from_template(template_text, env))
    else:
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
    g.add_argument("--template", default=None,
                   help="path to a .env.example to mirror line-for-line "
                        "(default: repo-root .env.example; falls back to a flat "
                        "render when absent)")
    g.set_defaults(func=_cmd_generate_env)

    c = sub.add_parser("check-ports",
                       help="fail (exit 3) if a host port the compose stack publishes is taken")
    c.add_argument("--config", required=True,
                   help="output of `docker compose config --format json`")
    c.add_argument("--raw", required=True,
                   help="output of `docker compose config --no-interpolate --format json`")
    c.add_argument("--docker-ps", default=None,
                   help="output of `docker ps --format` with project, name and ports columns")
    c.add_argument("--env-file", default=".env", help="named in the fix-it message")
    c.set_defaults(func=_cmd_check_ports)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

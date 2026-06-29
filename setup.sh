#!/usr/bin/env bash
# =============================================================================
# InferiaLLM — self-contained setup
# =============================================================================
# Installs Docker if missing (docker-ce, per-OS), generates a complete .env (if
# missing), brings up the InferiaLLM compose stack (app + postgres + redis),
# waits for local health, and verifies the public routing. Independent of the
# inferia-auth / worker / diffusion siblings.
#
#   ./setup.sh                      # interactive: prompt, generate .env, up, verify
#   ./setup.sh --yes --public-url https://inferiallm.example.com \
#              --superadmin-email admin@example.com
#   ./setup.sh --build              # force image rebuild
#   ./setup.sh --no-up              # only (re)generate .env, no Docker
#   ./setup.sh --no-install-docker  # never auto-install Docker; fail if missing
#   ./setup.sh --down               # stop the stack
#
# The heavy logic (secret-gen, validation, .env render) lives in
# scripts/setup/inferia_setup.py (pure, pytest-tested). This script is the
# orchestration around it.
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]:-$0}")" && pwd)"
HELPER="$SCRIPT_DIR/scripts/setup/inferia_setup.py"
COMPOSE_FILE="$SCRIPT_DIR/docker-compose.yml"
PYTHON="${PYTHON:-python3}"

# ---- defaults / state -------------------------------------------------------
ENV_FILE="${INFERIA_ENV_FILE:-$SCRIPT_DIR/.env}"
PUBLIC_URL=""
APP_PORT="8000"
APP_PORT_EXPLICIT=0
AUTH_MODE="local"
SUPERADMIN_EMAIL=""
PASSWORD=""
EXTERNAL_AUTH_URL=""
OAUTH_CLIENT_ID=""
WORKER_IMAGE_TAG="0.2.11"
HF_TOKEN=""
DO_BUILD=0
FORCE=0
NO_UP=0
ASSUME_YES=0
REQUIRE_PUBLIC=0
SKIP_PUBLIC=0
HEALTH_TIMEOUT=180
RESET_DB=0
DO_DOWN=0
FOLLOW_LOGS=0
GEN_PW=""
NO_INSTALL_DOCKER=0

# How docker is invoked. Normally just `docker`; if the daemon is only reachable
# via sudo right after a fresh install (the current shell isn't in the `docker`
# group yet) this is rewritten to `sudo docker` for the remainder of the run.
DOCKER=(docker)
# Populated by detect_os from /etc/os-release (overridable via OS_RELEASE_FILE).
OS_ID=""
OS_LIKE=""

# ---- output helpers ---------------------------------------------------------
# Colours are enabled only on a TTY and when NO_COLOR is unset (https://no-color.org).
if [[ -t 1 && -z "${NO_COLOR:-}" && "${TERM:-}" != "dumb" ]]; then
  C_RESET=$'\033[0m'; C_BOLD=$'\033[1m'; C_DIM=$'\033[2m'
  C_RED=$'\033[31m'; C_GREEN=$'\033[32m'; C_YELLOW=$'\033[33m'
  C_BLUE=$'\033[34m'; C_CYAN=$'\033[36m'; C_GRAY=$'\033[90m'
else
  C_RESET=""; C_BOLD=""; C_DIM=""; C_RED=""; C_GREEN=""
  C_YELLOW=""; C_BLUE=""; C_CYAN=""; C_GRAY=""
fi
_RULE="──────────────────────────────────────────────────────"

log()  { printf '%s•%s %s\n' "$C_CYAN" "$C_RESET" "$*"; }
ok()   { printf '  %s✓%s %s\n' "$C_GREEN" "$C_RESET" "$*"; }
warn() { printf '  %s!%s %s%s%s\n' "$C_YELLOW" "$C_RESET" "$C_YELLOW" "$*" "$C_RESET"; }
err()  { printf '  %s✗ %s%s\n' "$C_RED" "$*" "$C_RESET" >&2; }
die()  { err "$*"; exit 1; }
step() { printf '  %s→%s %s\n' "$C_BLUE" "$C_RESET" "$*"; }

# A section header: blank line, bold title, dim rule underneath.
section() {
  printf '\n%s%s▸ %s%s\n' "$C_BOLD" "$C_CYAN" "$1" "$C_RESET"
  printf '%s%s%s\n' "$C_GRAY" "$_RULE" "$C_RESET"
}

# An aligned key/value line for summaries.
kv() { printf '  %s%-16s%s %s\n' "$C_DIM" "$1" "$C_RESET" "$2"; }

banner() {
  # Inner width matches _RULE (54). Titles are ASCII so printf's byte-based
  # %-*s padding equals the display width (multibyte glyphs would misalign).
  local w=54 edge="${C_BOLD}${C_BLUE}" t1="   InferiaLLM  setup" t2="   one-command bring-up & verification"
  printf '\n%s╭%s╮%s\n' "$edge" "$_RULE" "$C_RESET"
  printf '%s│%s%s%-*s%s%s│%s\n' "$edge" "$C_RESET" "$C_BOLD" "$w" "$t1" "$C_RESET" "$edge" "$C_RESET"
  printf '%s│%s%s%-*s%s%s│%s\n' "$edge" "$C_RESET" "$C_DIM"  "$w" "$t2" "$C_RESET" "$edge" "$C_RESET"
  printf '%s╰%s╯%s\n' "$edge" "$_RULE" "$C_RESET"
}

usage() {
  cat <<'EOF'
setup.sh — InferiaLLM self-contained setup

Usage: ./setup.sh [flags]

Generation (used when .env is missing, or with --force):
  --public-url URL          Public base URL (e.g. https://inferiallm.example.com)
  --app-port N              App port (default 8000)
  --auth-mode MODE          local | inferiaauth | oidc (default local)
  --superadmin-email EMAIL  Superadmin email (default admin@<host>)
  --superadmin-password PW  Superadmin password (blank => strong random generated)
  --external-auth-url URL   IdP base URL (required for inferiaauth/oidc)
  --oauth-client-id ID      OAuth client id (required for inferiaauth/oidc)
  --worker-image-tag TAG    inferia-worker image tag (default 0.2.11)
  --hf-token TOKEN          Default HuggingFace token (optional)
  --force                   Regenerate .env (preserves secret key + DB password)
  --env-file PATH           Target .env path (default ./.env)

Lifecycle:
  --build                   docker compose up --build (force image rebuild)
  --no-up                   Only (re)generate .env; do not touch Docker
  --no-install-docker       Do not auto-install Docker; fail if it is missing
  --reset-db                docker compose down -v before bringing up (DESTROYS data)
  --down                    Stop the stack and exit
  --logs                    Follow app logs after a successful start

Verification:
  --timeout N               Seconds to wait for local /api/health (default 180)
  --require-public          Fail if any public route check fails
  --skip-public-check       Skip the public route checks entirely

Other:
  --yes, -y                 Non-interactive (requires --public-url)
  --help, -h                Show this help
EOF
}

# ---- arg parsing ------------------------------------------------------------
parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --public-url) PUBLIC_URL="$2"; shift 2 ;;
      --app-port) APP_PORT="$2"; APP_PORT_EXPLICIT=1; shift 2 ;;
      --auth-mode) AUTH_MODE="$2"; shift 2 ;;
      --superadmin-email) SUPERADMIN_EMAIL="$2"; shift 2 ;;
      --superadmin-password) PASSWORD="$2"; shift 2 ;;
      --external-auth-url) EXTERNAL_AUTH_URL="$2"; shift 2 ;;
      --oauth-client-id) OAUTH_CLIENT_ID="$2"; shift 2 ;;
      --worker-image-tag) WORKER_IMAGE_TAG="$2"; shift 2 ;;
      --hf-token) HF_TOKEN="$2"; shift 2 ;;
      --env-file) ENV_FILE="$2"; shift 2 ;;
      --timeout) HEALTH_TIMEOUT="$2"; shift 2 ;;
      --build) DO_BUILD=1; shift ;;
      --force) FORCE=1; shift ;;
      --no-up) NO_UP=1; shift ;;
      --no-install-docker) NO_INSTALL_DOCKER=1; shift ;;
      --reset-db) RESET_DB=1; shift ;;
      --down) DO_DOWN=1; shift ;;
      --logs) FOLLOW_LOGS=1; shift ;;
      --require-public) REQUIRE_PUBLIC=1; shift ;;
      --skip-public-check) SKIP_PUBLIC=1; shift ;;
      --yes|-y) ASSUME_YES=1; shift ;;
      --help|-h) usage; exit 0 ;;
      *) usage >&2; die "unknown argument: $1" ;;
    esac
  done
}

# ---- input collection -------------------------------------------------------
default_email_from_url() {
  # admin@<host> derived from the public URL netloc
  local host
  host="$(printf '%s' "$1" | sed -E 's#^[a-zA-Z]+://##; s#[:/].*$##')"
  printf 'admin@%s' "${host:-localhost}"
}

# Read a single value from ENV_FILE (last occurrence; surrounding quotes stripped).
_env_get() {
  [[ -f "$ENV_FILE" ]] || return 0
  local line val
  line="$(grep -E "^$1=" "$ENV_FILE" 2>/dev/null | tail -n1)" || true
  [[ -z "$line" ]] && return 0
  val="${line#*=}"
  val="${val%\"}"; val="${val#\"}"   # strip surrounding double quotes
  printf '%s' "$val"
}

# Recover the public origin from an existing .env: prefer the API gateway URL
# (origin + /api), then the control-plane URL, then the last absolute origin in
# ALLOWED_ORIGINS. Returns empty if only same-origin ("/api") values are present.
_derive_public_url() {
  local v
  v="$(_env_get DASHBOARD_API_GATEWAY_URL)"
  [[ -z "$v" ]] && v="$(_env_get INFERIA_CONTROL_PLANE_EXTERNAL_URL)"
  v="${v%/}"; v="${v%/api}"; v="${v%/}"
  if [[ "$v" =~ ^https?:// ]]; then printf '%s' "$v"; return 0; fi
  printf '%s' "$(_env_get ALLOWED_ORIGINS | tr ',' '\n' | grep -E '^https?://' | tail -n1)"
}

# When .env exists, source the verification targets from it so health/public
# checks and the summary reflect what the deployment actually serves. Explicit
# flags / prompted values still win (only empty fields are filled).
load_env_settings() {
  [[ -f "$ENV_FILE" ]] || return 0
  local v
  if [[ $APP_PORT_EXPLICIT -eq 0 ]]; then
    v="$(_env_get APP_PORT)"; [[ -n "$v" ]] && APP_PORT="$v"
  fi
  if [[ -z "$PUBLIC_URL" ]]; then
    v="$(_derive_public_url)"; [[ -n "$v" ]] && PUBLIC_URL="$v"
  fi
  if [[ -z "$SUPERADMIN_EMAIL" ]]; then
    v="$(_env_get SUPERADMIN_EMAIL)"; [[ -n "$v" ]] && SUPERADMIN_EMAIL="$v"
  fi
  return 0
}

collect_inputs() {
  local _p _a def_email
  if [[ $ASSUME_YES -eq 0 ]] && [[ -t 0 ]]; then
    if [[ -z "$PUBLIC_URL" ]]; then
      read -r -p "Public base URL (e.g. https://inferiallm.example.com): " PUBLIC_URL
    fi
    read -r -p "App port [${APP_PORT}]: " _p || true
    [[ -n "${_p:-}" ]] && APP_PORT="$_p"
    read -r -p "Auth mode (local/inferiaauth/oidc) [${AUTH_MODE}]: " _a || true
    [[ -n "${_a:-}" ]] && AUTH_MODE="$_a"
    if [[ "$AUTH_MODE" != "local" ]]; then
      [[ -z "$EXTERNAL_AUTH_URL" ]] && read -r -p "IdP base URL: " EXTERNAL_AUTH_URL
      [[ -z "$OAUTH_CLIENT_ID" ]] && read -r -p "OAuth client id: " OAUTH_CLIENT_ID
    fi
    if [[ -z "$SUPERADMIN_EMAIL" ]]; then
      def_email="$(default_email_from_url "$PUBLIC_URL")"
      read -r -p "Superadmin email [${def_email}]: " SUPERADMIN_EMAIL || true
      SUPERADMIN_EMAIL="${SUPERADMIN_EMAIL:-$def_email}"
    fi
    if [[ -z "$PASSWORD" ]]; then
      read -r -s -p "Superadmin password (blank => random): " PASSWORD || true
      echo
    fi
  fi
  if [[ -z "$PUBLIC_URL" ]]; then
    die "--public-url is required (interactive prompt unavailable)"
  fi
  if [[ -z "$SUPERADMIN_EMAIL" ]]; then
    SUPERADMIN_EMAIL="$(default_email_from_url "$PUBLIC_URL")"
  fi
  return 0
}

# ---- .env generation --------------------------------------------------------
generate_env() {
  section "Environment"
  local merge_args=()
  if [[ -f "$ENV_FILE" ]]; then
    if [[ $FORCE -eq 0 ]]; then
      ok "$ENV_FILE already exists — keeping it"
      step "use --force to regenerate (secrets + DB password are preserved)"
      return 0
    fi
    merge_args=(--merge "$ENV_FILE")
    step "Regenerating $ENV_FILE (preserving secret key + DB password)"
  fi

  collect_inputs

  kv "Public URL" "$PUBLIC_URL"
  kv "App port"   "$APP_PORT"
  kv "Auth mode"  "$AUTH_MODE"
  kv "Superadmin" "$SUPERADMIN_EMAIL"
  kv "Env file"   "$ENV_FILE"

  local args=(generate-env
    --public-url "$PUBLIC_URL"
    --app-port "$APP_PORT"
    --auth-mode "$AUTH_MODE"
    --email "$SUPERADMIN_EMAIL"
    --worker-image-tag "$WORKER_IMAGE_TAG")
  # Mirror the repo's .env.example line-for-line (comments + blanks preserved)
  # when it is present, so the generated .env stays readable and hand-editable.
  [[ -f "$SCRIPT_DIR/.env.example" ]] && args+=(--template "$SCRIPT_DIR/.env.example")
  [[ -n "$PASSWORD" ]]          && args+=(--password "$PASSWORD")
  [[ -n "$EXTERNAL_AUTH_URL" ]] && args+=(--external-auth-url "$EXTERNAL_AUTH_URL")
  [[ -n "$OAUTH_CLIENT_ID" ]]   && args+=(--oauth-client-id "$OAUTH_CLIENT_ID")
  [[ -n "$HF_TOKEN" ]]          && args+=(--hf-token "$HF_TOKEN")
  args+=("${merge_args[@]}")

  local tmp_out tmp_err
  tmp_out="$(mktemp)"; tmp_err="$(mktemp)"
  # shellcheck disable=SC2064
  trap "rm -f '$tmp_out' '$tmp_err'" RETURN

  if ! "$PYTHON" "$HELPER" "${args[@]}" >"$tmp_out" 2>"$tmp_err"; then
    cat "$tmp_err" >&2
    die "failed to generate .env"
  fi

  local tmp_env="${ENV_FILE}.tmp.$$"
  ( umask 077; cat "$tmp_out" >"$tmp_env" )
  mv -f "$tmp_env" "$ENV_FILE"
  chmod 600 "$ENV_FILE"
  ok "Wrote $ENV_FILE (chmod 600)"

  if grep -q '^GENERATED_SUPERADMIN_PASSWORD=' "$tmp_err"; then
    GEN_PW="$(sed -n 's/^GENERATED_SUPERADMIN_PASSWORD=//p' "$tmp_err")"
    ok "Generated a random superadmin password"
    printf '    %ssuperadmin password:%s %s%s%s\n' "$C_DIM" "$C_RESET" "$C_BOLD" "$GEN_PW" "$C_RESET"
    step "stored in $ENV_FILE as SUPERADMIN_PASSWORD — change it after first login"
  fi
}

# ---- docker helpers ---------------------------------------------------------
compose() { "${DOCKER[@]}" compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" "$@"; }

need_tool() { command -v "$1" >/dev/null 2>&1 || die "required tool not found: $1"; }

# Run a command with root privileges. Fatal if neither root nor sudo is usable.
as_root() {
  if [[ "$(id -u)" -eq 0 ]]; then "$@";
  elif command -v sudo >/dev/null 2>&1; then sudo "$@";
  else die "root privileges (or sudo) are required for: $*"; fi
}

# Best-effort privileged run: returns non-zero instead of dying when it can't.
try_root() {
  if [[ "$(id -u)" -eq 0 ]]; then "$@";
  elif command -v sudo >/dev/null 2>&1; then sudo "$@";
  else return 1; fi
}

# Populate OS_ID / OS_LIKE from os-release (path overridable for tests).
detect_os() {
  OS_ID=""; OS_LIKE=""
  local f="${OS_RELEASE_FILE:-/etc/os-release}"
  # os-release path is dynamic; sourced in subshells to read ID / ID_LIKE safely.
  if [[ -r "$f" ]]; then
    # shellcheck source=/dev/null
    OS_ID="$(. "$f" >/dev/null 2>&1; printf '%s' "${ID:-}")"
    # shellcheck source=/dev/null
    OS_LIKE="$(. "$f" >/dev/null 2>&1; printf '%s' "${ID_LIKE:-}")"
  fi
  [[ -n "$OS_ID" ]] || OS_ID="$(uname -s | tr '[:upper:]' '[:lower:]')"
}

# Normalize OS_ID + OS_LIKE to a package-manager family.
os_family() {
  case "$OS_ID" in
    ubuntu|debian|raspbian|kali|linuxmint|pop|elementary|zorin|neon) echo debian; return ;;
    fedora|fedora-asahi-remix)                                       echo fedora; return ;;
    centos|rhel|rocky|almalinux|ol|amzn)                             echo rhel;   return ;;
    arch|manjaro|endeavouros|garuda|cachyos|artix)                   echo arch;   return ;;
    opensuse*|sles|sled|suse)                                        echo suse;   return ;;
  esac
  case " $OS_LIKE " in
    *debian*|*ubuntu*)  echo debian; return ;;
    *rhel*|*centos*|*"fedora"*) echo rhel; return ;;
    *arch*)             echo arch;   return ;;
    *suse*)             echo suse;   return ;;
  esac
  echo unknown
}

# ---- per-OS docker-ce installers (each returns non-zero on any failure) -----
install_docker_debian() {
  local up codename arch
  case "$OS_ID" in
    debian|raspbian|kali) up=debian ;;
    *)                    up=ubuntu ;;   # ubuntu + its derivatives use the ubuntu repo
  esac
  # os-release path is dynamic; sourced in a subshell to read the codename.
  # shellcheck source=/dev/null
  codename="$(. "${OS_RELEASE_FILE:-/etc/os-release}" >/dev/null 2>&1
    if [[ "$up" == ubuntu ]]; then printf '%s' "${UBUNTU_CODENAME:-${VERSION_CODENAME:-}}"
    else printf '%s' "${VERSION_CODENAME:-}"; fi)"
  [[ -n "$codename" ]] || { warn "could not determine apt codename for $OS_ID"; return 1; }
  as_root apt-get update -y || return 1
  as_root apt-get install -y ca-certificates curl gnupg || return 1
  as_root install -m 0755 -d /etc/apt/keyrings || return 1
  curl -fsSL "https://download.docker.com/linux/$up/gpg" \
    | as_root gpg --batch --yes --dearmor -o /etc/apt/keyrings/docker.gpg || return 1
  as_root chmod a+r /etc/apt/keyrings/docker.gpg || return 1
  arch="$(dpkg --print-architecture 2>/dev/null || echo amd64)"
  printf 'deb [arch=%s signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/%s %s stable\n' \
    "$arch" "$up" "$codename" | as_root tee /etc/apt/sources.list.d/docker.list >/dev/null || return 1
  as_root apt-get update -y || return 1
  as_root apt-get install -y \
    docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin || return 1
}

install_docker_fedora() {
  as_root dnf -y install dnf-plugins-core || return 1
  as_root dnf -y config-manager --add-repo https://download.docker.com/linux/fedora/docker-ce.repo \
    || as_root dnf -y config-manager addrepo --from-repofile=https://download.docker.com/linux/fedora/docker-ce.repo \
    || return 1
  as_root dnf -y install \
    docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin || return 1
}

install_docker_rhel() {
  local mgr=yum; command -v dnf >/dev/null 2>&1 && mgr=dnf
  as_root "$mgr" -y install "$( [[ $mgr == dnf ]] && echo dnf-plugins-core || echo yum-utils )" || return 1
  as_root "$mgr" -y config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo \
    || as_root yum-config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo \
    || return 1
  as_root "$mgr" -y install \
    docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin || return 1
}

install_docker_arch() {
  # Arch ships upstream Docker as `docker`; `docker-compose` provides the v2 plugin.
  # Use -Syu (full upgrade): `pacman -Sy <pkg>` is a documented partial-upgrade
  # foot-gun — a refreshed DB + un-upgraded libs can yield a broken docker binary.
  # `--needed` skips anything already installed.
  as_root pacman -Syu --noconfirm --needed docker docker-compose docker-buildx || return 1
}

install_docker_suse() {
  as_root zypper --non-interactive install docker docker-compose || return 1
}

# Docker's official, OS-detecting convenience script — the universal fallback.
install_docker_convenience() {
  local tmp rc
  tmp="$(mktemp)" || return 1
  if ! curl -fsSL https://get.docker.com -o "$tmp"; then rm -f "$tmp"; return 1; fi
  as_root sh "$tmp"; rc=$?
  rm -f "$tmp"
  return $rc
}

# Try the native per-OS installer; fall back to the convenience script on failure.
_install_native_then_fallback() {
  local fn="$1"
  if "$fn"; then return 0; fi
  warn "Native Docker install ($fn) failed — falling back to get.docker.com"
  install_docker_convenience || die "Docker installation failed (native + convenience script)"
}

install_docker() {
  detect_os
  local fam; fam="$(os_family)"
  step "Installing Docker (detected: ${OS_ID:-unknown}, family: ${fam})"
  case "$fam" in
    debian) _install_native_then_fallback install_docker_debian ;;
    fedora) _install_native_then_fallback install_docker_fedora ;;
    rhel)   _install_native_then_fallback install_docker_rhel ;;
    arch)   _install_native_then_fallback install_docker_arch ;;
    suse)   _install_native_then_fallback install_docker_suse ;;
    *)
      warn "Unrecognized distro '${OS_ID:-?}' — using Docker's convenience script"
      install_docker_convenience || die "Docker installation failed (convenience script)"
      ;;
  esac
}

# Start the daemon if it isn't already reachable (systemd, then SysV fallback).
start_docker_daemon() {
  "${DOCKER[@]}" info >/dev/null 2>&1 && return 0
  if command -v systemctl >/dev/null 2>&1; then
    try_root systemctl enable --now docker >/dev/null 2>&1 \
      || try_root systemctl start docker >/dev/null 2>&1 || true
  elif command -v service >/dev/null 2>&1; then
    try_root service docker start >/dev/null 2>&1 || true
  fi
}

# Ensure the current shell can talk to the daemon; fall back to `sudo docker`
# for this run when the user isn't in the docker group yet (a fresh install
# needs a re-login for group membership to take effect).
ensure_docker_access() {
  "${DOCKER[@]}" info >/dev/null 2>&1 && return 0
  if [[ "$(id -u)" -ne 0 ]] && getent group docker >/dev/null 2>&1; then
    try_root usermod -aG docker "${USER:-$(id -un)}" >/dev/null 2>&1 || true
  fi
  if [[ "$(id -u)" -ne 0 ]] && command -v sudo >/dev/null 2>&1 \
     && sudo docker info >/dev/null 2>&1; then
    DOCKER=(sudo docker)
    warn "Using 'sudo docker' for this run — log out and back in to use docker without sudo."
    return 0
  fi
  return 1
}

# Make sure docker (+ compose v2) is installed and the daemon is reachable.
ensure_docker() {
  if command -v docker >/dev/null 2>&1; then
    ok "Docker is installed"
  elif [[ $NO_INSTALL_DOCKER -eq 1 ]]; then
    die "Docker is not installed (and --no-install-docker was given). Install Docker, then re-run."
  else
    install_docker
    command -v docker >/dev/null 2>&1 || die "Docker install did not produce a 'docker' binary"
    ok "Installed Docker"
  fi
  start_docker_daemon
  ensure_docker_access || die "the Docker daemon is not reachable (could not start it)."
  "${DOCKER[@]}" compose version >/dev/null 2>&1 \
    || die "'docker compose' (v2+) is required but is not available."
}

preflight() {
  section "Preconditions"
  need_tool curl
  need_tool "$PYTHON"
  ensure_docker
  ok "docker, docker compose, curl, $PYTHON available"
  ensure_network
  ensure_ssh_file
}

ensure_network() {
  if "${DOCKER[@]}" network inspect inferia-net >/dev/null 2>&1; then
    ok "Docker network 'inferia-net' present"
  else
    step "Creating external Docker network 'inferia-net'"
    "${DOCKER[@]}" network create inferia-net >/dev/null
    ok "Created 'inferia-net'"
  fi
}

ensure_ssh_file() {
  # The compose bind-mounts ./.ssh/authorized_keys; if it doesn't exist Docker
  # silently creates a *directory* there. Make sure it's a regular file.
  local f="$SCRIPT_DIR/.ssh/authorized_keys"
  mkdir -p "$SCRIPT_DIR/.ssh"
  if [[ -e "$f" ]]; then
    ok "SSH keys file present"
  else
    : >"$f"
    ok "Created empty .ssh/authorized_keys for the worker bind-mount"
  fi
}

stale_volume_guard() {
  # A freshly generated PG password won't authenticate against an existing
  # pgdata volume created with a different one.
  if [[ $FORCE -eq 0 ]] && "${DOCKER[@]}" volume inspect deploy_pgdata >/dev/null 2>&1; then
    warn "An existing 'deploy_pgdata' volume was found. If the DB password in"
    warn "$ENV_FILE differs from that volume's, Postgres auth will fail —"
    warn "run with --reset-db to recreate the volume (DESTROYS existing data)."
  fi
}

compose_up() {
  section "Docker stack"
  stale_volume_guard
  if [[ $RESET_DB -eq 1 ]]; then
    warn "--reset-db: removing volumes (postgres data, model cache, pulumi state)"
    compose down -v --remove-orphans || true
  fi
  local up=(up -d)
  [[ $DO_BUILD -eq 1 ]] && up+=(--build)
  step "docker compose ${up[*]}"
  compose "${up[@]}"
  ok "Containers started (app, postgres, redis)"
}

# ---- health + routing -------------------------------------------------------
wait_local_health() {
  section "Health check"
  local url="http://127.0.0.1:${APP_PORT}/api/health"
  local deadline=$(( SECONDS + HEALTH_TIMEOUT ))
  step "Waiting for ${url} (timeout ${HEALTH_TIMEOUT}s)"
  while (( SECONDS < deadline )); do
    if curl -fsS -o /dev/null "$url" 2>/dev/null; then
      ok "Local /api/health → 200"
      return 0
    fi
    sleep 3
  done
  err "Local health check timed out after ${HEALTH_TIMEOUT}s. Recent app logs:"
  compose logs --tail=80 app >&2 || true
  return 1
}

# probe URL; echo HTTP code. Retries a few times on a connection-level failure
# (000) so a transient TLS/connect hiccup doesn't flap the report.
http_code() {
  local c attempt
  for attempt in 1 2 3; do
    c="$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 "$1" 2>/dev/null)" || true
    c="${c:-000}"
    [[ "$c" != "000" ]] && break
    [[ $attempt -lt 3 ]] && sleep 1
  done
  printf '%s' "$c"
}

# Probe one public route and print an aligned result line. Relies on bash
# dynamic scoping for `base`/`failed` from the calling check_public_routes.
# $1=path  $2=extended-regex of acceptable codes
_check_route() {
  local code msg
  code="$(http_code "${base}$1")"
  msg="$(printf '%-15s %s' "$1" "$code")"
  if [[ "$code" =~ $2 ]]; then ok "$msg"; else warn "$msg"; failed=1; fi
}

check_public_routes() {
  section "Public routes"
  if [[ $SKIP_PUBLIC -eq 1 ]]; then
    step "Skipped (--skip-public-check)"
    return 0
  fi
  if [[ -z "$PUBLIC_URL" ]]; then
    warn "No public URL known — pass --public-url or set DASHBOARD_API_GATEWAY_URL in $ENV_FILE."
    step "Skipping public route checks."
    return 0
  fi
  local base="$PUBLIC_URL"; base="${base%/}"
  local failed=0 code msg

  step "Probing ${base}"

  _check_route "/"              '^200$'
  _check_route "/config.js"     '^200$'
  _check_route "/api/health"    '^200$'
  # inference is token-protected — 200 or 401 both mean "routed correctly"
  _check_route "/inf/v1/models" '^(200|401)$'
  # ollama OCI mirror lives at the root; anything but 404/000 means it's routed
  code="$(http_code "${base}/v2/")"
  msg="$(printf '%-15s %s' "/v2/" "$code")"
  if [[ "$code" != "404" && "$code" != "000" ]]; then ok "$msg"; else warn "$msg"; failed=1; fi

  if [[ $failed -eq 1 ]]; then
    if [[ $REQUIRE_PUBLIC -eq 1 ]]; then
      die "public route verification failed (--require-public)."
    fi
    warn "Some public routes did not verify — is the reverse proxy wired to inferia-app:${APP_PORT}?"
    warn "See deploy/README.md. (advisory; use --require-public to make this fatal)"
  fi
  return 0
}

summary() {
  local base="${PUBLIC_URL%/}"
  section "Summary"
  printf '  %s%s✓ InferiaLLM is up%s\n' "$C_BOLD" "$C_GREEN" "$C_RESET"
  echo
  kv "Dashboard"  "${base}/"
  kv "API health" "${base}/api/health"
  kv "Superadmin" "${SUPERADMIN_EMAIL}"
  [[ -n "$GEN_PW" ]] && kv "Password" "${GEN_PW}  ${C_DIM}(generated — change after login)${C_RESET}"
  kv "Env file"   "${ENV_FILE}"
  echo
  kv "Reverse proxy" "deploy/README.md — forward all paths to inferia-app:${APP_PORT}"
  kv "Manage"     "./setup.sh --down   ·   ./setup.sh --logs"
  echo
}

# ---- main -------------------------------------------------------------------
main() {
  parse_args "$@"
  [[ -f "$HELPER" ]] || die "helper not found: $HELPER"

  banner

  if [[ $DO_DOWN -eq 1 ]]; then
    preflight
    section "Docker stack"
    step "Stopping the stack"
    compose down
    ok "Stopped"
    return 0
  fi

  generate_env
  # If .env already existed (generation skipped), recover the verification
  # targets (public URL, app port, superadmin) from it.
  load_env_settings

  if [[ $NO_UP -eq 1 ]]; then
    log "--no-up: skipping Docker. Done."
    return 0
  fi

  preflight
  compose_up
  wait_local_health
  check_public_routes
  summary

  if [[ $FOLLOW_LOGS -eq 1 ]]; then
    compose logs -f app
  fi
}

# Only run main when executed (not when sourced for tests).
if [[ "${BASH_SOURCE[0]:-}" == "${0}" ]]; then
  main "$@"
fi

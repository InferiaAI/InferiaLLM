#!/usr/bin/env bash
# =============================================================================
# InferiaLLM — self-contained setup
# =============================================================================
# Generates a complete .env (if missing), brings up the InferiaLLM compose
# stack (app + postgres + redis), waits for local health, and verifies the
# public routing. Independent of the inferia-auth / worker / diffusion siblings.
#
#   ./setup.sh                      # interactive: prompt, generate .env, up, verify
#   ./setup.sh --yes --public-url https://inferiallm.example.com \
#              --superadmin-email admin@example.com
#   ./setup.sh --build              # force image rebuild
#   ./setup.sh --no-up              # only (re)generate .env, no Docker
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

# ---- output helpers ---------------------------------------------------------
log()  { printf '\033[0;36m[setup]\033[0m %s\n' "$*"; }
ok()   { printf '\033[0;32m[ ok ]\033[0m %s\n' "$*"; }
warn() { printf '\033[0;33m[warn]\033[0m %s\n' "$*"; }
err()  { printf '\033[0;31m[fail]\033[0m %s\n' "$*" >&2; }
die()  { err "$*"; exit 1; }

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
      --app-port) APP_PORT="$2"; shift 2 ;;
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
  local merge_args=()
  if [[ -f "$ENV_FILE" ]]; then
    if [[ $FORCE -eq 0 ]]; then
      log "$ENV_FILE already exists — keeping it (use --force to regenerate)."
      return 0
    fi
    merge_args=(--merge "$ENV_FILE")
    log "Regenerating $ENV_FILE (preserving secret key + DB password)."
  fi

  collect_inputs

  local args=(generate-env
    --public-url "$PUBLIC_URL"
    --app-port "$APP_PORT"
    --auth-mode "$AUTH_MODE"
    --email "$SUPERADMIN_EMAIL"
    --worker-image-tag "$WORKER_IMAGE_TAG")
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
  ok "Wrote $ENV_FILE (chmod 600)."

  if grep -q '^GENERATED_SUPERADMIN_PASSWORD=' "$tmp_err"; then
    GEN_PW="$(sed -n 's/^GENERATED_SUPERADMIN_PASSWORD=//p' "$tmp_err")"
    log "Generated superadmin password: ${GEN_PW}"
    log "  (also stored in $ENV_FILE as SUPERADMIN_PASSWORD — change it after first login)"
  fi
}

# ---- docker helpers ---------------------------------------------------------
compose() { docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" "$@"; }

need_tool() { command -v "$1" >/dev/null 2>&1 || die "required tool not found: $1"; }

preflight() {
  need_tool docker
  need_tool curl
  need_tool "$PYTHON"
  docker compose version >/dev/null 2>&1 || die "'docker compose' (v2+) is required"
  docker info >/dev/null 2>&1 || die "the Docker daemon is not reachable"
}

ensure_network() {
  if ! docker network inspect inferia-net >/dev/null 2>&1; then
    log "Creating external Docker network 'inferia-net'."
    docker network create inferia-net >/dev/null
  fi
}

ensure_ssh_file() {
  # The compose bind-mounts ./.ssh/authorized_keys; if it doesn't exist Docker
  # silently creates a *directory* there. Make sure it's a regular file.
  local f="$SCRIPT_DIR/.ssh/authorized_keys"
  mkdir -p "$SCRIPT_DIR/.ssh"
  [[ -e "$f" ]] || { : >"$f"; log "Created empty $f for the worker SSH bind-mount."; }
}

stale_volume_guard() {
  # A freshly generated PG password won't authenticate against an existing
  # pgdata volume created with a different one.
  if [[ $FORCE -eq 0 ]] && docker volume inspect deploy_pgdata >/dev/null 2>&1; then
    warn "An existing 'deploy_pgdata' volume was found. If the DB password in"
    warn "$ENV_FILE differs from that volume's, Postgres auth will fail —"
    warn "run with --reset-db to recreate the volume (DESTROYS existing data)."
  fi
}

compose_up() {
  ensure_network
  ensure_ssh_file
  stale_volume_guard
  if [[ $RESET_DB -eq 1 ]]; then
    warn "--reset-db: removing volumes (postgres data, model cache, pulumi state)."
    compose down -v --remove-orphans || true
  fi
  local up=(up -d)
  [[ $DO_BUILD -eq 1 ]] && up+=(--build)
  log "Starting the stack: docker compose ${up[*]}"
  compose "${up[@]}"
}

# ---- health + routing -------------------------------------------------------
wait_local_health() {
  local url="http://127.0.0.1:${APP_PORT}/api/health"
  local deadline=$(( SECONDS + HEALTH_TIMEOUT ))
  log "Waiting for ${url} (timeout ${HEALTH_TIMEOUT}s)..."
  while (( SECONDS < deadline )); do
    if curl -fsS -o /dev/null "$url" 2>/dev/null; then
      ok "Local health: ${url} 200"
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

check_public_routes() {
  [[ $SKIP_PUBLIC -eq 1 ]] && { log "Skipping public route checks (--skip-public-check)."; return 0; }
  local base="$PUBLIC_URL"; base="${base%/}"
  local failed=0 code

  log "Verifying public routes at ${base} ..."

  code="$(http_code "${base}/")"
  [[ "$code" == "200" ]] && ok "public /            ${code}" || { warn "public /            ${code}"; failed=1; }

  code="$(http_code "${base}/api/health")"
  [[ "$code" == "200" ]] && ok "public /api/health  ${code}" || { warn "public /api/health  ${code}"; failed=1; }

  code="$(http_code "${base}/config.js")"
  [[ "$code" == "200" ]] && ok "public /config.js   ${code}" || { warn "public /config.js   ${code}"; failed=1; }

  # inference may require a token (401) — both 200 and 401 mean "routed correctly"
  code="$(http_code "${base}/inf/v1/models")"
  [[ "$code" == "200" || "$code" == "401" ]] && ok "public /inf/v1/models ${code}" || { warn "public /inf/v1/models ${code}"; failed=1; }

  # ollama OCI mirror lives at the root; anything but 404 means it's routed
  code="$(http_code "${base}/v2/")"
  [[ "$code" != "404" && "$code" != "000" ]] && ok "public /v2/          ${code}" || { warn "public /v2/          ${code}"; failed=1; }

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
  echo
  ok "InferiaLLM is up."
  log "Dashboard:        ${base}/"
  log "API health:       ${base}/api/health"
  log "Superadmin email: ${SUPERADMIN_EMAIL}"
  [[ -n "$GEN_PW" ]] && log "Superadmin pass:  ${GEN_PW}  (generated — change after login)"
  log "Env file:         ${ENV_FILE}"
  log "Reverse proxy:    see deploy/README.md (forward all paths to inferia-app:${APP_PORT})"
  log "Manage:           ./setup.sh --down   |   ./setup.sh --logs"
}

# ---- main -------------------------------------------------------------------
main() {
  parse_args "$@"
  [[ -f "$HELPER" ]] || die "helper not found: $HELPER"

  if [[ $DO_DOWN -eq 1 ]]; then
    preflight
    log "Stopping the stack."
    compose down
    ok "Stopped."
    return 0
  fi

  generate_env

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

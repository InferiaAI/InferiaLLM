#!/usr/bin/env bash
# =============================================================================
# scripts/sso_smoke.sh — InferiaLLM + inferia-auth SSO end-to-end smoke.
# =============================================================================
# Brings the SSO stack up (make docker-up-sso), seeds a test user in
# inferia-auth, drives the full OAuth2 Authorization Code + PKCE flow with
# Playwright, and tears the stack down. Prints `ALL GOOD` on success.
#
# Preconditions (operator must satisfy these before running):
#   1. Docker is installed and the current user can run `docker compose`.
#   2. /etc/hosts contains:
#        127.0.0.1 inferia.local
#        127.0.0.1 auth.inferia.local
#      Without these the smoke cannot resolve the proxy hostnames.
#   3. Ports 80 and 443 on localhost are free.
#
# Environment overrides:
#   SMOKE_EMAIL     — login email (default: smoke@inferia.local)
#   SMOKE_PASSWORD  — login password (default: smoke-password-1234)
#   KEEP_STACK_UP   — set to "1" to skip `docker-down-sso` at the end
#                     (useful for manual debugging after a failed smoke).
# =============================================================================

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

SMOKE_EMAIL="${SMOKE_EMAIL:-smoke@inferia.local}"
SMOKE_PASSWORD="${SMOKE_PASSWORD:-smoke-password-1234}"
export SMOKE_EMAIL SMOKE_PASSWORD

# --- /etc/hosts precondition ---------------------------------------------------
# Fail fast with an actionable message rather than letting Playwright burn 60s
# trying to resolve inferia.local. We don't have sudo here so we can't fix it
# automatically; the operator must add the entries by hand.
if ! grep -qE "^[^#]*\binferia\.local\b" /etc/hosts; then
  cat <<'EOM' >&2
ERROR: /etc/hosts is missing the SSO hostnames.

Add the following lines to /etc/hosts (requires sudo) and re-run this script:

    127.0.0.1 inferia.local
    127.0.0.1 auth.inferia.local

See docs/operations/auth.md for the rationale.
EOM
  exit 2
fi

cleanup() {
  if [ "${KEEP_STACK_UP:-0}" != "1" ]; then
    echo "===> tearing down compose"
    make docker-down-sso >/dev/null 2>&1 || true
  else
    echo "===> KEEP_STACK_UP=1 → leaving the stack running for debugging"
    echo "     stop later with: make docker-down-sso"
  fi
}
trap cleanup EXIT

# --- Bring up the compose stack ------------------------------------------------
echo "===> docker compose up --build (this rebuilds inferia-auth and inferia-app on first run)"
make docker-up-sso

# --- Wait for both services to report healthy ---------------------------------
echo "===> waiting for /health on both hosts"
for url in https://auth.inferia.local/health https://inferia.local/health; do
  ok=0
  for i in $(seq 1 90); do
    if curl -skf "$url" >/dev/null 2>&1; then
      echo "  $url OK"
      ok=1
      break
    fi
    sleep 2
  done
  if [ "$ok" -ne 1 ]; then
    echo "  TIMEOUT waiting for $url — dumping last 200 lines of compose logs:"
    make docker-logs-sso 2>&1 | tail -200 || true
    exit 1
  fi
done

# --- Verify the gateway can reach inferia-auth's OIDC discovery ---------------
echo "===> verifying OIDC discovery reachable from inferia-app container"
docker compose -f deploy/docker-compose.sso.yml exec -T inferia-app \
  curl -sfk https://auth.inferia.local/.well-known/openid-configuration >/dev/null \
  || {
    echo "  inferia-app could not reach auth.inferia.local's discovery doc."
    echo "  This usually means the Caddy network alias isn't resolving inside"
    echo "  the inferia-app container. Check the sso-net aliases in"
    echo "  deploy/docker-compose.sso.yml."
    exit 1
  }
echo "  OIDC discovery reachable."

# --- Seed the test user --------------------------------------------------------
echo "===> seeding smoke user in inferia-auth"
seed_resp=$(curl -sk -o /tmp/sso_seed.out -w '%{http_code}' \
  -X POST https://auth.inferia.local/api/v1/auth/register \
  -H 'Content-Type: application/json' \
  -d "{\"email\":\"${SMOKE_EMAIL}\",\"password\":\"${SMOKE_PASSWORD}\",\"display_name\":\"Smoke User\"}")
case "$seed_resp" in
  2*)
    echo "  registered new user (${seed_resp})"
    ;;
  409)
    echo "  user already exists (409) — continuing"
    ;;
  *)
    echo "  unexpected register status: $seed_resp"
    cat /tmp/sso_seed.out
    exit 1
    ;;
esac

# --- Install Playwright deps (cached) -----------------------------------------
echo "===> installing playwright (npm + chromium browser)"
cd "${ROOT}/scripts"
if [ ! -d node_modules ]; then
  npm install --silent --no-audit --no-fund
fi
npx playwright install --with-deps chromium >/dev/null 2>&1 || \
  npx playwright install chromium >/dev/null 2>&1

# --- Drive the flow -----------------------------------------------------------
echo "===> running playwright smoke"
npx playwright test sso_smoke.spec.ts --reporter=list

echo ""
echo "ALL GOOD"

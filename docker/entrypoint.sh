#!/bin/bash
set -e

# Generate runtime config for the dashboard from env vars.
# This allows the pre-built image to work with any endpoint URLs
# specified at container run time instead of build time.
# Dashboard runtime config uses DASHBOARD_* prefixed env vars to avoid
# colliding with backend service URL env vars (e.g. INFERENCE_URL).
DASHBOARD_DIR=$(python -c "import inferia, os; print(os.path.join(inferia.__path__[0], 'dashboard'))" 2>/dev/null || true)
if [ -d "$DASHBOARD_DIR" ]; then
  python3 -c "
import json, os
config = {
    'API_GATEWAY_URL': os.environ.get('DASHBOARD_API_GATEWAY_URL', ''),
    'INFERENCE_URL': os.environ.get('DASHBOARD_INFERENCE_URL', ''),
    'WEB_SOCKET_URL': os.environ.get('DASHBOARD_WEB_SOCKET_URL', ''),
    'SIDECAR_URL': os.environ.get('DASHBOARD_SIDECAR_URL', ''),
}
print('window.__RUNTIME_CONFIG__ = ' + json.dumps(config) + ';')
" > "$DASHBOARD_DIR/config.js"
fi

# SERVICE_TYPE can be: filtration, inference, orchestration, unified
TYPE=${SERVICE_TYPE:-unified}

# Probe the DB schema directly rather than trusting a file-based marker.
# The previous `/data/.initialized` shortcut could de-sync from the actual
# DB state (e.g. when the pgdata volume is recreated but appdata survives),
# causing `inferiallm migrate` to run against an empty schema and fail with
# 'relation "public.<table>" does not exist' on the first ALTER.
SCHEMA_PRESENT=$(python3 - <<'PY' 2>/dev/null || echo "false"
import asyncio, os, sys
try:
    import asyncpg
except ImportError:
    print("false"); sys.exit(0)

async def check() -> str:
    try:
        conn = await asyncpg.connect(
            host=os.environ.get("PG_HOST", "localhost"),
            port=int(os.environ.get("PG_PORT", "5432")),
            user=os.environ.get("INFERIA_DB_USER", "inferia"),
            password=os.environ.get("INFERIA_DB_PASSWORD", "inferia"),
            database=os.environ.get("INFERIA_DB", "inferia"),
        )
        result = await conn.fetchval("SELECT to_regclass('public.organizations')")
        await conn.close()
        return "true" if result is not None else "false"
    except Exception:
        return "false"

print(asyncio.run(check()))
PY
)

if [ "$SCHEMA_PRESENT" = "true" ]; then
    echo "Schema present — running migrations..."
    inferiallm migrate
else
    echo "Schema missing — running first-time init..."
    inferiallm init
fi
# Keep the legacy marker for backward compatibility with any tooling that
# checks for it; harmless if it already exists.
mkdir -p /data && touch /data/.initialized

echo "Starting Inferia service: $TYPE"

case "$TYPE" in
  filtration)
    exec inferiallm start filtration
    ;;
  inference)
    exec inferiallm start inference
    ;;
  orchestration)
    echo "Starting Orchestration Stack..."
    exec inferiallm start orchestration
    ;;
  unified)
    exec inferiallm start
    ;;
  *)
    echo "Unknown SERVICE_TYPE: $TYPE. Falling back to exec $@"
    exec "$@"
    ;;
esac

#!/bin/bash
set -e

# Generate runtime config for the dashboard from DASHBOARD_* / AUTH_PROVIDER /
# EXTERNAL_AUTH_URL env vars. Safe to call when no dashboard is bundled in the
# image — exits 0 cleanly.
inferiallm write-dashboard-config

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

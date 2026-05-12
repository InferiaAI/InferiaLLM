#!/bin/bash
set -e

# Generate runtime config for the dashboard. Reads inferia.yaml's
# services.api_gateway.dashboard.* first, falls back to DASHBOARD_* env vars
# for any field the yaml leaves null. Safe to call when no dashboard is
# bundled in the image — exits 0 cleanly.
inferiallm write-dashboard-config

# SERVICE_TYPE can be: filtration, inference, orchestration, unified
TYPE=${SERVICE_TYPE:-unified}

if [ ! -f /data/.initialized ]; then
    echo "Running first-time init..."
    inferiallm init
    touch /data/.initialized
else
    echo "Running migrations..."
    inferiallm migrate
fi

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

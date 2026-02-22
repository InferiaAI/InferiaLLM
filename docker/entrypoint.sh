#!/bin/bash
set -e

# Generate runtime config for the dashboard from env vars.
# This allows the pre-built image to work with any endpoint URLs
# specified at container run time instead of build time.
DASHBOARD_DIR=$(python -c "import inferia, os; print(os.path.join(inferia.__path__[0], 'dashboard'))" 2>/dev/null || true)
if [ -d "$DASHBOARD_DIR" ]; then
  cat > "$DASHBOARD_DIR/config.js" <<EOF
window.__RUNTIME_CONFIG__ = {
  API_GATEWAY_URL: "${API_GATEWAY_URL:-}",
  INFERENCE_URL: "${INFERENCE_URL:-}",
  WEB_SOCKET_URL: "${WEB_SOCKET_URL:-}",
  SIDECAR_URL: "${SIDECAR_URL:-}",
};
EOF
fi

# SERVICE_TYPE can be: filtration, inference, orchestration, unified
TYPE=${SERVICE_TYPE:-unified}

if [ ! -f /data/.initialized ]; then
    echo "Running first-time init..."
    inferiallm init
    touch /data/.initialized
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

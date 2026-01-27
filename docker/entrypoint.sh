#!/bin/bash
set -e

# SERVICE_TYPE can be: filtration, inference, orchestration, unified
TYPE=${SERVICE_TYPE:-unified}

echo "Starting Inferia service: $TYPE"

case "$TYPE" in
  filtration)
    exec inferiallm filtration-gateway
    ;;
  inference)
    exec inferiallm inference-gateway
    ;;
  orchestration)
    # The orchestration service needs multiple processes
    # Gateway, Worker, and DePIN Sidecar
    echo "Starting Orchestration Stack..."
    inferiallm orchestration-start
    ;;
  unified)
    exec inferiallm api-start
    ;;
  *)
    echo "Unknown SERVICE_TYPE: $TYPE. Falling back to exec $@"
    exec "$@"
    ;;
esac

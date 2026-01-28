#!/bin/bash
set -e

# SERVICE_TYPE can be: filtration, inference, orchestration, unified
TYPE=${SERVICE_TYPE:-unified}

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

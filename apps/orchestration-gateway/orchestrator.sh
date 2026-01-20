#!/bin/bash

set -e

# Script directory (apps/orchestration-gateway)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$SCRIPT_DIR/../../services/orchestration"
PID_FILE="$SCRIPT_DIR/.service_pids"

# -----------------------------
# Utility functions
# -----------------------------
log() {
  echo "[orchestrator] $1"
}

kill_if_running() {
  local PID=$1
  local NAME=$2

  if [[ -n "$PID" ]] && kill -0 "$PID" 2>/dev/null; then
    log "Stopping $NAME (PID: $PID)"
    kill "$PID" || true
  else
    log "$NAME not running"
  fi
}

load_pids() {
  if [[ -f "$PID_FILE" ]]; then
    source "$PID_FILE"
  fi
}

save_pids() {
  cat <<EOF > "$PID_FILE"
MAIN_PID=$MAIN_PID
WORKER_PID=$WORKER_PID
NOSANA_PID=$NOSANA_PID
EOF
}

# -----------------------------
# START SERVICES
# -----------------------------
start_services() {
  log "Starting Inferia services..."

  # Database configuration matching docker-compose
  export POSTGRES_DSN="postgresql://inferia:inferia@localhost:5432/inferia"
  
  # Redis configuration matching docker-compose
  export REDIS_HOST="localhost"
  export REDIS_PORT="6379"
  export REDIS_USERNAME=""
  export REDIS_PASSWORD=""
  export FILTRATION_DATABASE_URL="postgresql://inferia:inferia@localhost:5432/inferia"

  # Nosana API Key for securing vLLM/Ollama deployments
  export NOSANA_INTERNAL_API_KEY="${NOSANA_INTERNAL_API_KEY:-nos-internal-secret-change-in-prod}"

  # Set PYTHONPATH to include both apps and services
  export PYTHONPATH="$ROOT_DIR/app:$SCRIPT_DIR"

  # Check for venv in services/orchestration
  if [[ ! -d "$ROOT_DIR/.venv" ]]; then
    echo "ERROR: .venv not found in $ROOT_DIR"
    exit 1
  fi

  source "$ROOT_DIR/.venv/bin/activate"

  # Orchestrator - runs from apps/orchestration-gateway
  log "Starting Orchestrator API"
  python3 "$SCRIPT_DIR/app.py" &
  MAIN_PID=$!

  # Worker - runs from services/orchestration
  log "Starting Deployment Worker"
  python3 "$ROOT_DIR/app/services/model_deployment/worker_main.py" &
  WORKER_PID=$!

  # Nosana sidecar
  log "Starting Nosana Sidecar"
  cd "$ROOT_DIR/app/services/nosana-sidecar"
  npx tsx src/server.ts &
  NOSANA_PID=$!
  cd "$SCRIPT_DIR"

  save_pids

  log "All services started"
  log "Orchestrator PID: $MAIN_PID"
  log "Worker PID: $WORKER_PID"
  log "Nosana PID: $NOSANA_PID"
}

# -----------------------------
# STOP SERVICES
# -----------------------------
stop_services() {
  log "Stopping services..."

  load_pids

  kill_if_running "$MAIN_PID" "Orchestrator"
  kill_if_running "$WORKER_PID" "Worker"
  kill_if_running "$NOSANA_PID" "Nosana"

  rm -f "$PID_FILE"

  # Fallback cleanup
  pkill -f "orchestration-gateway/app.py" 2>/dev/null || true
  pkill -f "worker_main.py" 2>/dev/null || true
  
  # Force kill port 3000 if still open
  if lsof -t -i:3000 >/dev/null 2>&1; then
      log "Cleaning up port 3000..."
      lsof -t -i:3000 | xargs kill -9 2>/dev/null || true
  fi

  # Force kill port 50051 (gRPC) if still open
  if lsof -t -i:50051 >/dev/null 2>&1; then
      log "Cleaning up port 50051..."
      lsof -t -i:50051 | xargs kill -9 2>/dev/null || true
  fi

  log "All services stopped"
}

# -----------------------------
# COMMAND ROUTER
# -----------------------------
case "$1" in
  start)
    start_services
    ;;
  stop)
    stop_services
    ;;
  restart)
    stop_services
    sleep 1
    start_services
    ;;
  *)
    echo "Usage: $0 {start|stop|restart}"
    exit 1
    ;;
esac

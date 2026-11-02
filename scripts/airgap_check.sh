#!/usr/bin/env bash
# Prove the model cache works with no internet.
#
# Needs an $APP image whose /v1/models/import endpoint exists; an older one
# fails at "import request rejected".
#
# Stage 1 (no GPU): a real huggingface_hub client on a Docker network created
# with --internal — no NAT, so huggingface.co is unreachable — downloads an
# imported model through the mirror.
#
# Stage 1-ollama (no GPU): a real ollama client on that network pulls a cached
# model from the /v2 mirror and answers a prompt on CPU.
#
# Stage 2 (needs a GPU): vLLM on the same network loads a real imported model,
# such as Qwen/Qwen2.5-0.5B-Instruct, and answers a completion.
#
#   ./scripts/airgap_check.sh stage1
#   ./scripts/airgap_check.sh stage1-ollama
#   ./scripts/airgap_check.sh stage2 <model-id>
#   ./scripts/airgap_check.sh clean
set -euo pipefail

APP=${APP:-inferia-app}
NET=airgap-check
MODEL=${MODEL:-airgap/demo}
STAGE_DIR=/var/lib/inferia/models/import/airgap-demo
IMAGE=$(docker inspect "$APP" --format '{{.Config.Image}}')
OLLAMA_MODEL=${OLLAMA_MODEL:-qwen2:0.5b}
OLLAMA_IMAGE=${OLLAMA_IMAGE:-ollama/ollama:latest}
OLLAMA_C=airgap-ollama
VLLM_IMAGE=${VLLM_IMAGE:-docker.io/vllm/vllm-openai:v0.22.1}
VLLM_C=airgap-vllm
VLLM_TIMEOUT=${VLLM_TIMEOUT:-900}

note() { printf '\n== %s\n' "$1"; }
fail() { printf 'FAIL  %s\n' "$1"; exit 1; }
pass() { printf 'PASS  %s\n' "$1"; }

need_key() {
  # `|| true`: under pipefail a non-matching grep would exit before fail() runs.
  KEY=$(grep -E '^INTERNAL_API_KEY=' .env | cut -d= -f2- | tr -d '"' | tr -d '\r' || true)
  [ -n "$KEY" ] || fail "INTERNAL_API_KEY not found in .env"
  # Orchestration's host port (docker-compose.yml publishes 127.0.0.1:<this>:8080).
  ORCH_PORT=$(grep -E '^ORCHESTRATION_HOST_PORT=' .env | cut -d= -f2- | tr -d '"' | tr -d '\r' || true)
  ORCH_PORT=${ORCH_PORT:-8080}
}

need_egress_off() {
  # The control plane is not isolated, so with egress on a miss comes from
  # origin. Asked of the code, not the env: an old image ignores the variable.
  docker exec "$APP" python -c \
    "from orchestration.config import settings; raise SystemExit(0 if settings.model_egress is False else 1)" \
    2>/dev/null \
    || fail "$APP must run an image with the egress switch, and INFERIA_MODEL_EGRESS=false"
}

ensure_net() {
  note "network with no route to the internet"
  docker network inspect "$NET" >/dev/null 2>&1 || docker network create --internal "$NET" >/dev/null
  docker network connect "$NET" "$APP" 2>/dev/null || true
  pass "network $NET ready, $APP attached"
}

stage1() {
  need_key
  need_egress_off
  ensure_net

  note "stage a model and import it"
  docker exec "$APP" sh -c "
    rm -rf $STAGE_DIR && mkdir -p $STAGE_DIR
    printf '{\"model_type\":\"llama\",\"hidden_size\":16}' > $STAGE_DIR/config.json
    head -c 65536 /dev/urandom > $STAGE_DIR/model.safetensors
  "
  curl -sf -o /dev/null -X POST http://localhost:${ORCH_PORT}/v1/models/import \
    -H 'Content-Type: application/json' \
    -H "X-Internal-API-Key: $KEY" -H 'X-Gateway-Request: true' \
    -d "{\"format\":\"hf\",\"from\":\"airgap-demo\",\"model_id\":\"$MODEL\"}" \
    || fail "import request rejected"

  for _ in $(seq 1 30); do
    status=$(docker exec "$APP" python -c "
import asyncio, os, asyncpg
async def m():
    c = await asyncpg.connect(os.environ['DATABASE_URL'].replace('+asyncpg',''))
    print(await c.fetchval(\"SELECT status FROM model_cache WHERE model_id=\$1\", '$MODEL') or '')
    await c.close()
asyncio.run(m())" 2>/dev/null | tr -d '\r')
    [ "$status" = "cached" ] && break
    [ "$status" = "error" ] && fail "import failed; check the row's error column"
    sleep 1
  done
  [ "$status" = "cached" ] || fail "import did not reach cached (last: ${status:-none})"
  pass "imported and cached"

  note "client with no internet downloads it through the mirror"
  docker run --rm --network "$NET" \
    -e HF_ENDPOINT="http://$APP:8000/api/hf" \
    -e HF_HUB_DISABLE_TELEMETRY=1 \
    --entrypoint python \
    "$IMAGE" -c "
import socket, sys
# Prove there is no egress before trusting the result below.
try:
    socket.create_connection(('huggingface.co', 443), timeout=5)
    sys.exit('FAIL  the network reached huggingface.co; it is not isolated')
except OSError:
    print('PASS  huggingface.co is unreachable')

from huggingface_hub import snapshot_download, hf_hub_download, file_exists

# Engines probe for optional files on every load. This answers False only if
# the mirror's X-Error-Code reaches the client; a bare 404 raises instead.
assert file_exists('$MODEL', 'special_tokens_map.json') is False
print('PASS  an absent file resolves as absent, not as an error')

p = hf_hub_download('$MODEL', 'config.json')
assert open(p).read().startswith('{'), 'config.json came back wrong'
print('PASS  hf_hub_download')

d = snapshot_download('$MODEL')
import os
names = sorted(os.listdir(d))
assert 'model.safetensors' in names, names
assert os.path.getsize(os.path.join(d, 'model.safetensors')) == 65536
print('PASS  snapshot_download, weights byte-exact')
" || fail "the offline client could not fetch the model"

  note "stage 1 complete — the mirror serves with no internet"
  echo "next: import a real model, then ./scripts/airgap_check.sh stage2 <model-id>   (needs a GPU host)"
}

stage1_ollama() {
  docker image inspect "$OLLAMA_IMAGE" >/dev/null 2>&1 \
    || fail "pull $OLLAMA_IMAGE on the host first (the test network has no internet)"

  need_egress_off

  note "the model is already in the control-plane cache"
  curl -sf -o /dev/null \
    -H 'Accept: application/vnd.docker.distribution.manifest.v2+json' \
    "http://localhost:8000/v2/library/${OLLAMA_MODEL%%:*}/manifests/${OLLAMA_MODEL##*:}" \
    || fail "$OLLAMA_MODEL is not in the cache; import or pre-warm it first"
  pass "/v2 serves its manifest"

  ensure_net

  note "a real ollama client with no internet pulls from the mirror"
  docker rm -f "$OLLAMA_C" >/dev/null 2>&1 || true
  docker run -d --name "$OLLAMA_C" --network "$NET" "$OLLAMA_IMAGE" >/dev/null
  for _ in $(seq 1 30); do
    docker exec "$OLLAMA_C" ollama list >/dev/null 2>&1 && break
    sleep 1
  done
  docker exec "$OLLAMA_C" ollama list >/dev/null 2>&1 || fail "ollama did not start"

  # Prove there is no egress before trusting anything below.
  if docker exec "$OLLAMA_C" \
       timeout 5 bash -c "echo > /dev/tcp/registry.ollama.ai/443" 2>/dev/null; then
    fail "the network reached registry.ollama.ai; it is not isolated"
  fi
  pass "registry.ollama.ai is unreachable"

  # --insecure: the harness talks plain http to a container, where a real
  # install points INFERIA_MODEL_MIRROR_BASE at https.
  REF="$APP:8000/library/$OLLAMA_MODEL"
  # Quiet on success; its progress bar otherwise buries every other result.
  PULL_LOG=$(docker exec "$OLLAMA_C" ollama pull --insecure "$REF" 2>&1) || {
    printf '%s\n' "$PULL_LOG" | tail -5
    fail "ollama pull failed"
  }
  pass "ollama pull"

  note "and runs it on CPU"
  # stdout only: the spinner goes to stderr, and merging it makes any
  # non-empty check pass on terminal escape codes alone.
  OUT=$(docker exec "$OLLAMA_C" timeout 300 ollama run "$REF" "Reply with one word: ok" 2>/dev/null)
  printf '%s' "$OUT" | grep -q '[A-Za-z]' || fail "the model produced no text"
  printf 'PASS  inference answered: %s\n' "$(printf '%s' "$OUT" | tr -d '\r\n' | head -c 60)"

  note "stage 1 ollama complete — a model ran with no internet"
  echo "run ./scripts/airgap_check.sh clean to remove the container and network"
}

vllm_logs_and_fail() {
  docker logs --tail 30 "$VLLM_C" 2>&1 | sed 's/^/    /'
  docker rm -f "$VLLM_C" >/dev/null 2>&1 || true
  fail "$1"
}

stage2() {
  local model=${1:-}
  [ -n "$model" ] || fail "usage: $0 stage2 <model-id>   (import it first)"
  docker image inspect "$VLLM_IMAGE" >/dev/null 2>&1 \
    || fail "pull $VLLM_IMAGE on the host first (the test network has no internet)"
  need_egress_off
  ensure_net

  note "vLLM on the isolated network, weights from the mirror only"
  docker rm -f "$VLLM_C" >/dev/null 2>&1 || true
  local started=$(date +%s)
  # Detached: the server never exits; a foreground run could only fail.
  docker run -d --name "$VLLM_C" --gpus all --network "$NET" \
    -e HF_ENDPOINT="http://$APP:8000/api/hf" \
    "$VLLM_IMAGE" \
    --model "$model" --max-model-len 512 --enforce-eager >/dev/null

  for _ in $(seq 1 30); do
    [ "$(docker inspect "$VLLM_C" --format '{{.State.Running}}' 2>/dev/null)" = "true" ] \
      && break
    sleep 1
  done
  # A check that merely failed to run would otherwise read as "unreachable".
  probe=$(docker exec "$VLLM_C" bash -c \
    'timeout 5 bash -c "echo > /dev/tcp/huggingface.co/443" && echo reached || echo blocked' \
    2>/dev/null)
  [ "$probe" = "blocked" ] \
    || vllm_logs_and_fail "egress check in $VLLM_C said: ${probe:-could not run}"
  pass "huggingface.co is unreachable"

  note "waiting for the weights to arrive through the mirror"
  local ready=""
  for _ in $(seq 1 "$VLLM_TIMEOUT"); do
    docker exec "$VLLM_C" python3 -c \
      'import urllib.request as u; u.urlopen("http://127.0.0.1:8000/v1/models", timeout=2)' \
      2>/dev/null && { ready=1; break; }
    [ "$(docker inspect "$VLLM_C" --format '{{.State.Running}}' 2>/dev/null)" = "true" ] \
      || vllm_logs_and_fail "vLLM exited before it served"
    sleep 1
  done
  [ -n "$ready" ] || vllm_logs_and_fail "vLLM did not serve within ${VLLM_TIMEOUT}s"
  printf 'PASS  served in %ss (transfer plus load)\n' "$(( $(date +%s) - started ))"

  note "and answers a completion"
  local out
  out=$(docker exec "$VLLM_C" python3 -c "
import json, urllib.request
b = json.dumps({'model': '$model', 'prompt': 'Reply with one word: ok',
                'max_tokens': 8}).encode()
r = urllib.request.Request('http://127.0.0.1:8000/v1/completions', b,
                           {'Content-Type': 'application/json'})
print(json.load(urllib.request.urlopen(r, timeout=180))['choices'][0]['text'])
" 2>/dev/null)
  printf '%s' "$out" | grep -q '[A-Za-z]' || vllm_logs_and_fail "no completion came back"
  printf 'PASS  completion: %s\n' "$(printf '%s' "$out" | tr -d '\r\n' | head -c 60)"

  docker rm -f "$VLLM_C" >/dev/null 2>&1 || true
  note "stage 2 complete — vLLM ran with no internet"
}

clean() {
  docker rm -f "$OLLAMA_C" "$VLLM_C" >/dev/null 2>&1 || true
  docker exec "$APP" sh -c "rm -rf $STAGE_DIR" 2>/dev/null || true
  docker network disconnect "$NET" "$APP" 2>/dev/null || true
  docker network rm "$NET" 2>/dev/null || true
  echo "removed the network and staging; the cache row is left for inspection"
}

case "${1:-}" in
  stage1)        stage1 ;;
  stage1-ollama) stage1_ollama ;;
  stage2)        stage2 "${2:-}" ;;
  clean)         clean ;;
  *) echo "usage: $0 {stage1|stage1-ollama|stage2 [model-id]|clean}"; exit 2 ;;
esac

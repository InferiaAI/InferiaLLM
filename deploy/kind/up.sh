#!/usr/bin/env bash
# Bring up the llm-d data-plane spike on kind: Gateway API Inference Extension
# (InferencePool) + llm-d router (Envoy sidecar + EPP) + llm-d-inference-sim pods
# publishing KV-cache events. CPU only. See deploy/kind/README.md.
#
#   ./deploy/kind/up.sh          # create or converge; safe to re-run
#
# Every step checks before it acts, so a re-run converges instead of failing.
set -euo pipefail

CLUSTER="${CLUSTER:-inferia-s3}"
NAMESPACE="${NAMESPACE:-inferia-llmd}"
RELEASE="${RELEASE:-spike}"
HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]:-$0}")" && pwd)"

# ---- pinned versions (official sources are listed in README.md) -------------
KIND_VERSION="v0.33.0"
KIND_NODE_IMAGE="kindest/node:v1.37.0@sha256:a1ed56cfb0e7b93589bdf97c8cd566405a265939e3620fc4f5de89adff580ae5"
GIE_VERSION="v1.6.2"
GIE_CRDS_URL="https://github.com/kubernetes-sigs/gateway-api-inference-extension/releases/download/${GIE_VERSION}/v1-manifests.yaml"
GIE_CRDS_SHA256="f410dfca474608c02903087b47f1ac081706dfb6dfe7f83d00a7cc7e00c4ad0d"
LLMD_ROUTER_VERSION="v1.0.0"
LLMD_CRDS_URL="https://github.com/llm-d/llm-d-router/releases/download/${LLMD_ROUTER_VERSION}/manifests.yaml"
LLMD_CRDS_SHA256="bae0ae787ec97494ff21933882810fb49bdec88b2cf6fe14539ffeedd2ba21e3"
ROUTER_CHART="oci://ghcr.io/llm-d/charts/llm-d-router-standalone"
ROUTER_CHART_VERSION="v1.0.0"
# Free disk needed to create the cluster (node image + router/envoy/sim images).
MIN_FREE_GB="${MIN_FREE_GB:-2}"

step() { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }
ok()   { printf '  \033[32mok\033[0m %s\n' "$*"; }
die()  { printf '\n\033[31mFAILED: %s\033[0m\n' "$*" >&2; exit 1; }

# Always the cluster this script made, whatever kubectl's current context is.
kubectl() { command kubectl --context "kind-$CLUSTER" "$@"; }
helm()    { command helm --kube-context "kind-$CLUSTER" "$@"; }

# Download a manifest and refuse it unless it matches the pinned digest.
fetch_verified() {
  local url="$1" sha="$2" out="$3"
  curl -fsSL --retry 3 -o "$out" "$url" || die "download failed: $url"
  echo "$sha  $out" | sha256sum -c --quiet - || die "checksum mismatch for $url"
}

# ---- 1. tools and machine --------------------------------------------------
step "Checking tools and the machine"
for t in docker kind kubectl helm curl sha256sum; do
  command -v "$t" >/dev/null || die "missing '$t' (see README.md: Prerequisites)"
done
[[ "$(kind version | awk '{print $2}')" == "$KIND_VERSION" ]] \
  || echo "  note: kind is $(kind version | awk '{print $2}'), pinned $KIND_VERSION"
docker info >/dev/null 2>&1 || die "docker is not reachable"
ok "tools present"

cluster_exists() { kind get clusters 2>/dev/null | grep -qx "$CLUSTER"; }

if ! cluster_exists; then
  free_gb=$(df -Pk /var/lib/docker 2>/dev/null | awk 'NR==2{print int($4/1048576)}')
  [[ -n "$free_gb" ]] || free_gb=$(df -Pk / | awk 'NR==2{print int($4/1048576)}')
  (( free_gb >= MIN_FREE_GB )) \
    || die "only ${free_gb} GB free for Docker; need >= ${MIN_FREE_GB} GB (MIN_FREE_GB to override)"
  for port in 18911 18912; do
    ! ss -ltn "sport = :$port" | grep -q LISTEN || die "host port 127.0.0.1:$port is in use"
  done
  ok "${free_gb} GB free; host ports 18911/18912 free"
fi

# ---- 2. cluster ------------------------------------------------------------
step "Creating the kind cluster '$CLUSTER'"
if cluster_exists; then
  ok "already exists"
else
  kind create cluster --name "$CLUSTER" --image "$KIND_NODE_IMAGE" \
    --config "$HERE/kind-config.yaml" --wait 120s
  ok "created"
fi
kubectl wait --for=condition=Ready node --all --timeout=120s >/dev/null
ok "node ready ($(kubectl version -o json | sed -n 's/.*"gitVersion": "\(v[^"]*\)".*/\1/p' | tail -1))"

# ---- 3. CRDs ---------------------------------------------------------------
step "Installing CRDs (GIE $GIE_VERSION InferencePool, llm-d $LLMD_ROUTER_VERSION InferenceObjective)"
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
fetch_verified "$GIE_CRDS_URL" "$GIE_CRDS_SHA256" "$tmp/gie.yaml"
fetch_verified "$LLMD_CRDS_URL" "$LLMD_CRDS_SHA256" "$tmp/llmd.yaml"
kubectl apply --server-side -f "$tmp/gie.yaml" -f "$tmp/llmd.yaml" >/dev/null
kubectl wait --for=condition=Established --timeout=60s \
  crd/inferencepools.inference.networking.k8s.io \
  crd/inferenceobjectives.llm-d.ai >/dev/null
ok "CRDs established"

# ---- 4. model servers (sims) -----------------------------------------------
step "Deploying llm-d-inference-sim pods in '$NAMESPACE'"
kubectl create namespace "$NAMESPACE" --dry-run=client -o yaml | kubectl apply -f - >/dev/null
kubectl -n "$NAMESPACE" apply -f "$HERE/manifests/sim.yaml" >/dev/null
kubectl -n "$NAMESPACE" rollout status deployment/sim --timeout=180s
ok "sims ready"

# ---- 5. llm-d router -------------------------------------------------------
step "Installing the llm-d router ($ROUTER_CHART $ROUTER_CHART_VERSION)"
helm upgrade --install "$RELEASE" "$ROUTER_CHART" --version "$ROUTER_CHART_VERSION" \
  -n "$NAMESPACE" -f "$HERE/values/router-precise-prefix.values.yaml" --wait --timeout 5m >/dev/null
kubectl -n "$NAMESPACE" rollout status "deployment/${RELEASE}-epp" --timeout=180s
ok "router ready"

# ---- 6. smoke --------------------------------------------------------------
step "Smoke test through both entry points"
body='{"model":"sim-model","prompt":"hello from up.sh","max_tokens":4}'
for ep in "llm-d router|http://127.0.0.1:18911" "baseline|http://127.0.0.1:18912"; do
  name="${ep%%|*}"; url="${ep#*|}"
  for i in $(seq 1 30); do
    pod=$(curl -fsS -o /dev/null -D - -H 'Content-Type: application/json' -d "$body" \
            "$url/v1/completions" 2>/dev/null | tr -d '\r' | awk -F': ' 'tolower($1)=="x-inference-pod"{print $2}') && [[ -n "$pod" ]] && break
    sleep 2
  done
  [[ -n "${pod:-}" ]] || die "$name ($url) did not serve a completion"
  ok "$name served by $pod"
done

printf '\n\033[1;32mUp.\033[0m  llm-d router: http://127.0.0.1:18911   baseline: http://127.0.0.1:18912\n'
printf 'Routing demo: python3 %s/demo/prefix_routing.py\n' "$HERE"

#!/usr/bin/env bash
# Take a GPU machine to a cluster InferiaLLM can deploy models on.
#
# Expects a host that already has the NVIDIA driver, Docker and the NVIDIA
# container toolkit — an AWS Deep Learning Base AMI (Ubuntu) has all three.
# Run it from a checkout:
#
#   ./deploy/gpu-kind.sh              # everything
#   ./deploy/gpu-kind.sh --no-engine  # skip pre-loading the engine image
#
# Every step checks before it acts, so a re-run continues where it stopped.
set -euo pipefail

CLUSTER="${CLUSTER:-inferia}"
REPO_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]:-$0}")/.." && pwd)"
# Matches the vLLM image in src/orchestration/recipes/engines.yaml.
ENGINE_IMAGE="${ENGINE_IMAGE:-docker.io/vllm/vllm-openai:v0.22.1}"
PUBLIC_URL="${PUBLIC_URL:-http://localhost:8000}"
SUPERADMIN_EMAIL="${SUPERADMIN_EMAIL:-admin@example.com}"
PULL_ENGINE=1
[[ "${1:-}" == "--no-engine" ]] && PULL_ENGINE=0

step() { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }
ok()   { printf '  \033[32mok\033[0m %s\n' "$*"; }
die()  { printf '\n\033[31mFAILED: %s\033[0m\n' "$*" >&2; exit 1; }

# Always the cluster this script made, whatever kubectl's current context is.
kubectl() { command kubectl --context "kind-$CLUSTER" "$@"; }

# ---- 1. the machine ---------------------------------------------------------
step "Checking the machine"
[[ "$(uname -s)" == "Linux" && "$(uname -m)" == "x86_64" ]] \
  || die "this expects linux/x86_64; found $(uname -s)/$(uname -m)"
command -v nvidia-smi >/dev/null \
  || die "no nvidia-smi: no GPU, or the driver is missing"
command -v nvidia-ctk >/dev/null \
  || die "no nvidia-ctk: install the NVIDIA container toolkit"
nvidia-smi --query-gpu=name,memory.total --format=csv,noheader \
  || die "the GPU is not usable"
docker info >/dev/null 2>&1 \
  || die "docker is not usable by this user (usermod -aG docker \$USER, then log in again)"
avail=$(df -BG --output=avail / | tail -1 | tr -dc '0-9')
[[ "$avail" -ge 60 ]] || die "only ${avail}G free on /: the engine image needs room"
ok "GPU, docker and ${avail}G free disk"

# ---- 2. tools ---------------------------------------------------------------
# type -P, not command -v: the kubectl wrapper above is a shell function, and
# command -v would find it and skip installing the binary it calls.
step "Installing kind, kubectl and helm if missing"
if ! type -P kind >/dev/null; then
  curl -fsSLo /tmp/kind https://kind.sigs.k8s.io/dl/v0.31.0/kind-linux-amd64
  sudo install -m 0755 /tmp/kind /usr/local/bin/kind
fi
if ! type -P kubectl >/dev/null; then
  curl -fsSLo /tmp/kubectl \
    "https://dl.k8s.io/release/$(curl -fsSL https://dl.k8s.io/release/stable.txt)/bin/linux/amd64/kubectl"
  sudo install -m 0755 /tmp/kubectl /usr/local/bin/kubectl
fi
type -P helm >/dev/null \
  || curl -fsSL https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash
for tool in kind kubectl helm; do
  type -P "$tool" >/dev/null || die "$tool did not install"
done
ok "kind, kubectl and helm are present"

# ---- 3. let a container see the GPU ----------------------------------------
# A kind node is a container, so the GPU has to be passed into it. The toolkit
# does that when docker's default runtime is nvidia, and the volume-mount form
# is what lets the node claim every GPU on the host.
step "Pointing docker at the NVIDIA runtime"
if ! docker info 2>/dev/null | grep -q 'Default Runtime: nvidia'; then
  sudo nvidia-ctk runtime configure --runtime=docker --set-as-default
  sudo systemctl restart docker
fi
sudo nvidia-ctk config --in-place \
  --set accept-nvidia-visible-devices-as-volume-mounts=true
docker info 2>/dev/null | grep -q 'Default Runtime: nvidia' \
  || die "docker's default runtime is still not nvidia"
ok "docker default runtime is nvidia"

# ---- 4. the cluster ---------------------------------------------------------
step "Creating the kind cluster '$CLUSTER'"
if kind get clusters 2>/dev/null | grep -qx "$CLUSTER"; then
  ok "already exists"
else
  cat <<EOF | kind create cluster --name "$CLUSTER" --config -
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
nodes:
  - role: control-plane
    extraMounts:
      # Asks the toolkit for every GPU on the host.
      - hostPath: /dev/null
        containerPath: /var/run/nvidia-container-devices/all
EOF
fi
# The toolkit's hook calls ldconfig.real, which the node image does not ship.
docker exec "${CLUSTER}-control-plane" ln -sf /sbin/ldconfig /sbin/ldconfig.real
docker exec "${CLUSTER}-control-plane" nvidia-smi -L >/dev/null 2>&1 \
  || die "the node container cannot see the GPU: step 3 did not take effect"
ok "the node can see the GPU"

# ---- 5. tell Kubernetes about the GPU --------------------------------------
# The host driver stays, but the toolkit has to be the operator's: it is what
# gives containerd inside the node an "nvidia" runtime. Without it every GPU
# pod fails to start with `no runtime for "nvidia" is configured`.
step "Installing the NVIDIA GPU operator"
helm repo add nvidia https://nvidia.github.io/gpu-operator >/dev/null 2>&1 || true
helm repo update >/dev/null
helm upgrade --install gpu-operator nvidia/gpu-operator \
  --namespace gpu-operator --create-namespace \
  --set driver.enabled=false \
  --set toolkit.enabled=true \
  --wait --timeout 15m

# Minutes, not seconds: the operator pulls several GB first.
echo "  waiting for the node to advertise a GPU (up to 15 minutes)"
for _ in $(seq 180); do
  gpus=$(kubectl get node "${CLUSTER}-control-plane" \
    -o jsonpath='{.status.allocatable.nvidia\.com/gpu}' 2>/dev/null || true)
  [[ -n "$gpus" && "$gpus" != "0" ]] && break
  sleep 5
done
[[ -n "${gpus:-}" && "$gpus" != "0" ]] \
  || die "the node still advertises no nvidia.com/gpu"
ok "the node advertises $gpus GPU"

step "Running a pod that uses the GPU"
kubectl delete pod gpu-check --ignore-not-found >/dev/null
kubectl run gpu-check --restart=Never \
  --image=nvidia/cuda:12.4.1-base-ubuntu22.04 \
  --overrides='{"spec":{"containers":[{"name":"gpu-check","image":"nvidia/cuda:12.4.1-base-ubuntu22.04","command":["nvidia-smi","-L"],"resources":{"limits":{"nvidia.com/gpu":"1"}}}]}}' \
  >/dev/null
kubectl wait --for=jsonpath='{.status.phase}'=Succeeded pod/gpu-check --timeout=300s \
  || { kubectl describe pod gpu-check; die "the test pod could not use the GPU"; }
kubectl logs gpu-check
kubectl delete pod gpu-check >/dev/null
ok "a pod can use the GPU"

# ---- 6. KEDA ------------------------------------------------------------------
step "Installing KEDA"
helm repo add kedacore https://kedacore.github.io/charts >/dev/null 2>&1 || true
helm repo update >/dev/null
helm upgrade --install keda kedacore/keda \
  --namespace keda --create-namespace --wait --timeout 10m
ok "KEDA is up"

# ---- 7. InferiaLLM ----------------------------------------------------------
step "Starting InferiaLLM"
cd "$REPO_DIR"
./setup.sh --yes \
  --public-url "$PUBLIC_URL" \
  --superadmin-email "$SUPERADMIN_EMAIL" \
  --k8s-cluster "$CLUSTER"

step "Installing Prometheus"
# It scrapes the control plane, which runs in compose rather than in the
# cluster. A pod reaches the host through the cluster network's gateway, whose
# address is not fixed, so it is read rather than assumed.
gateway="$(docker inspect "${CLUSTER}-control-plane" \
  -f '{{range .NetworkSettings.Networks}}{{.Gateway}}{{"\n"}}{{end}}' 2>/dev/null \
  | grep -m1 '\.')"
[[ -n "$gateway" ]] || die "could not read the node's gateway address"
# Values in .env may be quoted.
app_port="$(sed -n 's/^APP_PORT=//p' "$REPO_DIR/.env" 2>/dev/null \
  | tail -1 | tr -d '"'"'"'\r')"
ok "control plane reachable from pods at ${gateway}:${app_port:-8000}"
sed "s#CONTROL_PLANE_ADDRESS#${gateway}:${app_port:-8000}#" \
  "$REPO_DIR/deploy/k8s/prometheus.yaml" | kubectl apply -f -
kubectl -n monitoring rollout status deploy/prometheus --timeout=300s
ok "Prometheus is up"

# ---- 8. the engine image ----------------------------------------------------
# About 9 GB, fetched now so the first deployment does not wait for it. The
# node pulls it itself: `kind load` fails converting docker's image format.
if [[ $PULL_ENGINE -eq 1 ]]; then
  step "Pulling $ENGINE_IMAGE onto the node (several minutes)"
  # crictl prints repository and tag in separate columns.
  if docker exec "${CLUSTER}-control-plane" crictl images 2>/dev/null \
      | awk '{print $1":"$2}' | grep -qx "$ENGINE_IMAGE"; then
    ok "already on the node"
  else
    docker exec "${CLUSTER}-control-plane" crictl pull "$ENGINE_IMAGE" \
      || die "the node could not pull $ENGINE_IMAGE"
    ok "pulled onto the node"
  fi
fi

cat <<EOF

$(printf '\033[1;32mReady.\033[0m')

  Dashboard, from your laptop:
    ssh -i <key.pem> -L 8000:localhost:8000 ubuntu@<this machine's public ip>
    then open http://localhost:8000

  Superadmin:  $SUPERADMIN_EMAIL
  Password:    grep '^SUPERADMIN_PASSWORD=' $REPO_DIR/.env

  Next: create a Kubernetes compute pool, then deploy a model on vLLM.

EOF

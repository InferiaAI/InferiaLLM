#!/usr/bin/env bash
# CPU-safe release-readiness demonstration.  Run after s3/dataplane integration.
set -euo pipefail

run_simulator=0
run_helm=0
run_kind=0

usage() {
  cat <<'EOF'
Usage: scripts/demo/platform_readiness_demo.sh [--simulator] [--helm] [--kind]

Default: verify the platform documentation and render all DOT diagrams.
--simulator: validate and run the tiny inferia-sim scenario (SIMULATED output).
--helm: render the Helm chart only; it does not install anything.
--kind: create a local kind llm-d demo cluster, run the routing demo, then clean up.
EOF
}

while (($#)); do
  case "$1" in
    --simulator) run_simulator=1 ;;
    --helm) run_helm=1 ;;
    --kind) run_kind=1 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
cd "$root"

test -d docs/platform/diagrams
for source in docs/platform/diagrams/*.dot; do
  command -v dot >/dev/null || { echo "Graphviz dot is required" >&2; exit 1; }
  dot -Tsvg "$source" -o /tmp/"$(basename "${source%.dot}")".svg
done
echo "Documentation diagrams rendered successfully."

if ((run_simulator)); then
  test -x "$(command -v inferia-sim)" || { echo "inferia-sim is required" >&2; exit 1; }
  inferia-sim validate tiny
  inferia-sim run tiny --seed 1 --out /tmp/inferia-sim-demo --check-invariants
  echo "Simulator output is SIMULATED; it is not GPU performance evidence."
fi

if ((run_helm)); then
  command -v helm >/dev/null || { echo "helm is required" >&2; exit 1; }
  helm lint deploy/helm/inferiallm
  helm template inferiallm deploy/helm/inferiallm > /tmp/inferiallm-rendered.yaml
  echo "Helm render complete; no Kubernetes resources were installed."
fi

cleanup_kind() {
  if ((run_kind)) && [[ -x deploy/kind/down.sh ]]; then
    deploy/kind/down.sh || true
  fi
}

if ((run_kind)); then
  test -x deploy/kind/up.sh || { echo "Integrated deploy/kind demo is required" >&2; exit 1; }
  trap cleanup_kind EXIT
  deploy/kind/up.sh
  python3 deploy/kind/demo/prefix_routing.py --out /tmp/inferia-kind-prefix-routing
  echo "Kind result: MEASURED (platform), engine timing simulated."
fi

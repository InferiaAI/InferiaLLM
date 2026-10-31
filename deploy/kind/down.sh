#!/usr/bin/env bash
# Delete the spike's kind cluster and verify nothing is left behind.
#
#   ./deploy/kind/down.sh      # safe to re-run; succeeds when the cluster is already gone
set -euo pipefail

CLUSTER="${CLUSTER:-inferia-s3}"

ok()  { printf '  \033[32mok\033[0m %s\n' "$*"; }
die() { printf '\n\033[31mFAILED: %s\033[0m\n' "$*" >&2; exit 1; }

if kind get clusters 2>/dev/null | grep -qx "$CLUSTER"; then
  kind delete cluster --name "$CLUSTER"
else
  ok "cluster '$CLUSTER' not present"
fi

# Verify: no cluster, no node containers, no kubeconfig context, host ports released.
! kind get clusters 2>/dev/null | grep -qx "$CLUSTER" || die "cluster '$CLUSTER' still listed"
left=$(docker ps -a --filter "label=io.x-k8s.kind.cluster=$CLUSTER" -q)
[[ -z "$left" ]] || die "kind node containers still present: $left"
! command kubectl config get-contexts -o name 2>/dev/null | grep -qx "kind-$CLUSTER" \
  || die "kubeconfig context kind-$CLUSTER still present"
for port in 18911 18912; do
  ! ss -ltn "sport = :$port" | grep -q LISTEN || die "host port $port still in use"
done
ok "cluster '$CLUSTER' gone; no containers, context or host ports left"

# CPU-safe platform demo runbook

This runbook demonstrates architecture and platform behaviour without making
GPU-performance claims. Run it from an integrated checkout that contains the
referenced committed implementation branches.

## 1. Validate the simulator

```sh
inferia-sim list-scenarios
inferia-sim validate tiny
inferia-sim run tiny --seed 1 --out /tmp/inferia-sim-tiny --check-invariants
```

Show `summary.json` and `manifest.json`. Describe the output as **SIMULATED**.

## 2. Demonstrate cache-aware platform routing

Install the pinned `docker`, `kind`, `kubectl`, `helm` and Python prerequisites
from [`deploy/kind/README.md`](../../deploy/kind/README.md), then run:

```sh
./deploy/kind/up.sh
python3 deploy/kind/demo/prefix_routing.py --out /tmp/inferia-kind-prefix-routing
./deploy/kind/down.sh
```

Show the generated result manifest and compare the router to the baseline.
Describe this as **MEASURED (platform), engine timing simulated**.

## 3. Review self-hosted configuration without installing

```sh
helm template inferiallm deploy/helm/inferiallm > /tmp/inferiallm-rendered.yaml
```

This is a render check, not a deployed-cluster proof. Discuss secrets, PVC,
network policy, optional backup and llm-d prerequisites from the chart README.

## 4. Explain the calibration handoff

```sh
bash bench/lease/run_lease.sh --dry-run --base-url http://127.0.0.1:8000
```

Do not use `--execute` in a CPU demo. End by showing the pending calibration
gate in [capacity/report.md](capacity/report.md).

# Integrated platform release-readiness checklist

**Audit target:** `s3/dataplane` at `36f0e0a` (2026-11-03).

This is a release-review plan, not evidence that the branch has been merged to
`main` or that GPU validation has run.

## 1. Integration order

1. Create the integration PR from `s3/dataplane` to `main`. It already contains
   the simulator, enterprise deployment and platform-foundation merges
   (`5e09a8d`, `de79fac`, `d35cacb`). Do **not** separately cherry-pick
   `s2/simulator` or `s4/enterprise-ui` after it; that would duplicate their
   changes.
2. Resolve and merge the `s3/dataplane` PR after the verification gates below.
3. Bring across only the unique WS1 review artifacts from `s1/spec-docs`, or
   merge with the documented resolutions below. Do not overwrite newer S3
   operational evidence with older WS1 prose.
4. Run the documentation and demo checks on the resulting integration commit;
   record its SHA in release notes and in all generated GPU manifests.

## 2. Known overlap and resolution policy

Three-way analysis against common base `681a157` reports conflicts or
same-path additions in these areas:

| Path | Why it overlaps | Resolution |
|---|---|---|
| `docs/platform/spec.md` | S3 records later CPU evidence; WS1 adds traceability framing | Keep S3's current implementation facts; retain WS1's explicit evidence/merge caveats where still true |
| `docs/platform/ops.md` | S3 adds local restore and Redis/Postgres drill evidence | Keep S3's drill details; retain WS1's safety warning and capacity link |
| `docs/platform/capacity/report.md` | Both document non-GPU evidence | Prefer S3's reproducible results and sweeps; preserve the no-extrapolation calibration gate |
| `docs/platform/implementation-status.md` | Both contain evidence ledger rows | Keep the newer S3 rows, including KV-offload contract and bounded kind load smoke |
| `docs/platform/README.md` | Both are platform entry points | Keep S3 implementation links and add links to WS1 traceability/review material |
| `docs/platform/diagrams/README.md` and diagram assets | Two complete diagram sets use different filenames/detail levels | Select one complete set; do not ship duplicated numbering. Preserve DOT source and re-render every selected SVG |

`docs/platform/interfaces.md` should retain v1.4 from S3: it documents
server-owned workload classes and prevents client-controlled llm-d priority.

WS1-only review artifacts—`traceability.md`, `evidence-audit.md`,
`cross-link-validation.md`, `demo-runbook.md`, `presentation.md`, and this
file—have no S3 counterpart and should be retained after a link refresh.

## 3. Verification gates

Run in an environment with project dependencies installed:

```sh
PYTHONPATH=src pytest -q \
  src/tests/simulator \
  src/tests/providers/k8s \
  src/tests/inference/test_llmd_routing_headers.py \
  src/tests/api_gateway/test_usage_stream_worker.py \
  src/tests/common/test_extensions_registry.py \
  src/tests/common/test_workload.py \
  src/tests/setup/test_port_preflight.py

python -m unittest deploy/kind/demo/test_prefix_routing.py
bash bench/lease/run_lease.sh --dry-run --base-url http://127.0.0.1:8000
helm lint deploy/helm/inferiallm
helm template inferiallm deploy/helm/inferiallm > /tmp/inferiallm-rendered.yaml
for source in docs/platform/diagrams/*.dot; do
  dot -Tsvg "$source" -o /tmp/"$(basename "${source%.dot}")".svg
done
npm --prefix apps/dashboard run build
```

The kind routing demo is opt-in because it creates a local cluster:

```sh
./deploy/kind/up.sh
python3 deploy/kind/demo/prefix_routing.py --out /tmp/inferia-kind-prefix-routing
./deploy/kind/down.sh
```

Review generated manifests, result labels and cleanup output. The kind result
is **MEASURED (platform), engine timing simulated**.

## 4. GPU-only and environment-gated items

- Real vLLM calibration and the simulator error/MAPE report.
- Real-engine KV offload, HBM pressure and recovery validation.
- Real multimodal-engine smoke tests.
- Cross-node/P/D fabric measurement.
- Production-cluster backup PVC/CronJob and HA/restore drills.
- The unverified 10k-concurrency kind exercise.

None of these is satisfied by simulator, Helm-render, or CPU kind evidence.

## 5. Demo path

Use `scripts/demo/platform_readiness_demo.sh` after integration. Its default
mode performs only local documentation checks; `--simulator`, `--helm` and
`--kind` are explicit opt-ins. `--kind` always attempts cleanup on exit and
must not be used against a cluster that is outside this demo's scope.

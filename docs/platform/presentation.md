# Platform review outline

## 1. Architecture decision

InferiaLLM owns tenancy, governance, orchestration, packaging and capacity
evidence; it adopts engines, Kubernetes and inference-aware routing. Use the
[system-context diagram](diagrams/01-system-context.svg).

## 2. Request path and boundaries

Walk through [request lifecycle](diagrams/02-request-response-lifecycle.svg)
and [control/data-plane separation](diagrams/03-control-plane-vs-data-plane.svg).
State that Redis usage delivery is at-least-once and async; it is not part of
the synchronous token path.

## 3. Data-plane proof

Show the kind prefix-routing demo and its manifest. The proof is routing and
cache-affinity behaviour on a CPU simulator backend—not GPU performance.

## 4. Scale model and caveat

Use the KV, resource, distributed-inference and autoscaling diagrams. These
are architecture/model views. Simulator outputs are **SIMULATED** until GPU
calibration records an error budget.

## 5. Operability and enterprise seams

Show deployment, recovery and multimodal diagrams; then the extension registry
and Helm render. Distinguish committed code in the worktrees from integrated
mainline delivery.

## 6. Close with explicit gates

- GPU calibration and real-engine saturation/multimodal smoke.
- Cluster-backed backup/restore and HA exercises.
- Merge, CI and release validation of the implementation worktrees.

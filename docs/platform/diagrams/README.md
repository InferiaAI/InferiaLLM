# Platform diagrams

Graphviz DOT is the source of truth for these diagrams. Render each source with:

```sh
for source in *.dot; do dot -Tsvg "$source" -o "${source%.dot}.svg"; done
```

The SVG is committed beside its source so the engineering specification remains
readable without a local Graphviz installation. Diagrams must distinguish
current behaviour from a target or TODO capability and must not introduce
performance claims without an evidence label.

Current coverage:

1. System context
2. Request/response lifecycle
3. Control plane vs data plane
4. Deployment orchestration routes
5. GPU/CPU resource allocation
6. KV-cache lifecycle
7. Distributed inference and inter-node communication

### Notes on the current batch

- The request diagram separates the implemented cached request checks from two
  explicit WS2 targets: cancellation propagation and asynchronous usage
  handling. It does not claim either target is delivered.
- The control/data-plane diagram records the resilience boundary: running
  endpoints serve without a synchronous Postgres write on each request, while
  orchestration continues to provide endpoint configuration out of band.
- The deployment diagram is a route-selection view, not a state-machine
  guarantee. Provider readiness and persisted lifecycle state remain provider-
  specific; its direct-adapter, reconciler and self-registration labels mirror
  the current deployment-server routing contract.
- The resource-allocation diagram shows established provider provisioning around
  an explicitly target-state placement feedback loop. It does not claim a
  queue/KV/TTFT placement scorer is implemented.
- The KV and distributed-inference diagrams are target architecture views for
  WS3/WS4. They distinguish engine-resident paged KV from proposed offload,
  cache-aware routing, P/D disaggregation, and cross-node KV transfer.

# Platform diagrams

Graphviz DOT is the source of truth for these diagrams. Render each source with:

```sh
dot -Tsvg 01-system-context.dot -o 01-system-context.svg
```

The SVG is committed beside its source so the engineering specification remains
readable without a local Graphviz installation. Diagrams must distinguish
current behaviour from a target or TODO capability and must not introduce
performance claims without an evidence label.

| # | Diagram |
|---|---|
| 01 | System context |
| 02 | Request lifecycle |
| 03 | Control plane versus data plane |
| 04 | Deployment and orchestration |
| 05 | Resource allocation |
| 06 | KV cache lifecycle |
| 07 | Distributed inference |
| 08 | Autoscaling and routing |
| 09 | Failure recovery |
| 10 | Multimodal inference |

# Platform diagrams

Graphviz DOT is the source of truth for these diagrams. Render each source with:

```sh
dot -Tsvg 01-system-context.dot -o 01-system-context.svg
```

The SVG is committed beside its source so the engineering specification remains
readable without a local Graphviz installation. Diagrams must distinguish
current behaviour from a target or TODO capability and must not introduce
performance claims without an evidence label.

"""llm-d data-plane mode of the Kubernetes adapter (plan D2 / WS2, session-3 T2).

A pool whose metadata sets ``dataplane: "llm-d"`` serves each deployment through
llm-d: model-server pods selected by a Gateway API Inference Extension
``InferencePool``, fronted by an llm-d router (Envoy + the EPP endpoint picker)
that routes by prefix-cache overlap from the model servers' KV-cache events.

``resources`` builds the objects (pure); ``dataplane`` applies, watches and
deletes them. RBAC is a one-time namespace bootstrap
(``deploy/kind/manifests/llmd-namespace-bootstrap.yaml``), so orchestration never
needs RBAC verbs. Versions are pinned in ``resources`` and ``_chart_envoy``.
"""

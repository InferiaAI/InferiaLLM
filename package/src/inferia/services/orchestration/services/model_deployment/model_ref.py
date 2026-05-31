"""Resolve a deployment's worker ``artifact_uri`` from its persisted fields.

Single source of truth shared by the warm-deploy path
(``deployment_server._model_spec_from_request``) and the EC2-bootstrap path
(``deployment_linker._spec_from_pending``). Both previously diverged and BOTH
omitted ``configuration.model_id`` — the field where ollama/localai deploys
store the real ``name:tag`` — so they shipped the human display name to the
worker, which rejected it.

Resolution order (first non-empty wins):
  1. ``configuration["model"]["artifact_uri"]``
  2. ``configuration["artifact_uri"]``
  3. ``configuration["model_id"]``      (ollama/localai: bare ``name:tag``)
  4. ``inference_model``
  5. ``model_name``

The worker's recipe validation (inferia-worker ``recipes.go``) requires a
``scheme://`` from {s3,gs,hf,http,https,oci}. Engines with their own registry
(ollama/localai) carry a bare ``name:tag``; the worker strips the scheme
before pulling from that registry, so any allowed scheme works. ``hf://`` is
the worker's canonical scheme (see inferia-worker ``recipes_test.go`` which
uses ``hf://llama3`` etc. for ollama). We therefore prepend ``hf://`` when the
resolved identifier has no scheme of its own.
"""
from __future__ import annotations

__all__ = ["resolve_artifact_uri"]

_DEFAULT_SCHEME = "hf"


def resolve_artifact_uri(
    *,
    configuration: dict | None,
    inference_model: str | None = None,
    model_name: str | None = None,
) -> str | None:
    """Return a scheme-prefixed artifact_uri, or ``None`` when unresolvable."""
    cfg = configuration if isinstance(configuration, dict) else {}
    model_block = cfg.get("model")
    if not isinstance(model_block, dict):
        model_block = {}

    candidate = (
        model_block.get("artifact_uri")
        or cfg.get("artifact_uri")
        or cfg.get("model_id")
        or inference_model
        or model_name
    )
    if candidate is None:
        return None
    uri = str(candidate).strip()
    if not uri:
        return None
    if "://" not in uri:
        uri = f"{_DEFAULT_SCHEME}://{uri}"
    return uri

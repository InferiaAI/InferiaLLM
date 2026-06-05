"""Per-deploy decision: fetch the model from the CP mirror or from origin.

The control plane owns the cache state and pre-warms on every deploy, so at
load-spec build time it knows whether it has the model (cached) or is fetching
it (downloading/pending). In those cases it points the worker at its mirror;
otherwise (error/absent) the worker pulls from origin.
"""
from __future__ import annotations

_HF_RECIPES = ("vllm", "tei", "infinity")
_MIRROR_STATES = ("cached", "downloading", "pending")


def derive_cache_key(recipe: str, artifact_uri: str) -> tuple[str, str, str]:
    """Map (recipe, artifact_uri) -> (source, model_id, revision) — the SAME
    natural key the deploy pre-warm uses (deployment_server.deploy_model)."""
    raw = str(artifact_uri).split("://", 1)[-1]  # strip any scheme
    if recipe == "ollama":
        name, _, tag = raw.partition(":")
        return ("ollama", name, tag or "latest")
    return ("hf", raw, "main")


def choose_fetch_source(cache_row: dict | None) -> str:
    """'mirror' if the CP has the model or is fetching it; else 'origin'."""
    if cache_row and cache_row.get("status") in _MIRROR_STATES:
        return "mirror"
    return "origin"


def apply_mirror_to_spec(spec: dict, *, recipe: str, mirror_base: str) -> None:
    """Inject mirror coordinates into a load spec (mutates in place).

    HF engines: set env HF_ENDPOINT=<base>/hf so huggingface_hub (inside the
    vLLM/TEI container) pulls through the CP. Ollama: rewrite artifact_uri to a
    host-prefixed ref so `ollama pull` hits the CP /v2 registry mirror.
    """
    base = mirror_base.rstrip("/")
    if recipe in _HF_RECIPES:
        spec.setdefault("env", {})["HF_ENDPOINT"] = base + "/hf"
    elif recipe == "ollama":
        host = base.split("://", 1)[-1].rstrip("/")
        raw = str(spec["model"]["artifact_uri"]).split("://", 1)[-1]
        spec["model"]["artifact_uri"] = (
            f"{host}/{raw}" if "/" in raw else f"{host}/library/{raw}"
        )


async def resolve_and_apply_mirror(
    spec: dict, *, recipe: str, artifact_uri: str, mirror_base: str, cache_repo
) -> None:
    """Look up the CP cache row and inject mirror coords iff the CP has/IS-getting
    the model. No-op when mirror_base is blank (dormant) or cache_repo is None."""
    if not mirror_base or cache_repo is None:
        return
    source, model_id, revision = derive_cache_key(recipe, artifact_uri)
    try:
        row = await cache_repo.get_by_key(source=source, model_id=model_id, revision=revision)
    except Exception:
        row = None  # cache lookup failure -> behave as 'origin' (never block deploy)
    if choose_fetch_source(row) == "mirror":
        apply_mirror_to_spec(spec, recipe=recipe, mirror_base=mirror_base)

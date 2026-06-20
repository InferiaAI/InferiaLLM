"""Shared Envoy cluster naming logic for xDS and worker routing.

Both the orchestration service's xDS control plane (which publishes cluster
configurations) and the inference service's worker routing (which builds routing
headers) must compute identical cluster names for the same (pool_id, engine, model)
tuple. This module provides the single authoritative implementation.

Cluster naming follows:
  * ``grp-<pool_id>-<engine>-<model>`` — pooled node with a specific model
  * ``grp-<pool_id>-<engine>`` — pooled node with no specific model
  * ``grp-<engine>-<model>`` — singleton node with a specific model
  * ``inferia-workers`` — singleton node with no specific model

Both pool_id and engine can be None/falsy. Model defaults to "__default__"
if not provided or empty.
"""
from __future__ import annotations

from typing import Optional


def sanitize_cluster_name(value: Optional[str]) -> str:
    """Sanitize a value for use in an Envoy cluster name.
    
    Keeps alphanumeric, dash, underscore, and dot. Replaces other characters
    with dash. Matches xDS and worker routing expectations.
    
    Args:
        value: The value to sanitize (e.g., pool_id, engine, model).
        
    Returns:
        Sanitized string safe for Envoy cluster names.
    """
    if not value:
        return ""
    return "".join(c if c.isalnum() or c in "-_." else "-" for c in str(value))


def build_envoy_cluster_name(
    pool_id: Optional[str] = None,
    engine: Optional[str] = None,
    model: Optional[str] = None,
) -> str:
    """Build the Envoy cluster name for a deployment.
    
    Computes the cluster name based on pool membership and model. This must
    match exactly between xDS (cluster publication) and worker routing
    (header generation), or requests will 503 with "no cluster found".
    
    Args:
        pool_id: The pool ID if this node is pooled, else None for singleton.
        engine: The inference engine (vllm, ollama, etc.), can be None.
        model: The model/deployment identifier, defaults to "__default__".
        
    Returns:
        The Envoy cluster name (e.g., "grp-pool1-vllm-gemma").
        
    Raises:
        ValueError: If neither pool_id nor engine is provided (degenerate case).
    """
    # Normalize model: empty/None → "__default__"
    model = model or "__default__"
    
    # Sanitize all parts (may become empty strings)
    safe_pool = sanitize_cluster_name(pool_id) if pool_id else ""
    safe_engine = sanitize_cluster_name(engine) if engine else ""
    safe_model = sanitize_cluster_name(model) if model and model != "__default__" else ""
    
    # Build cluster name based on pool/singleton and model
    # Use list comprehension to skip empty parts, then join with dashes
    if pool_id:
        # Pooled node
        if safe_model and model != "__default__":
            # Has specific model: grp-{pool}-{engine}-{model}
            parts = [safe_pool, safe_engine, safe_model]
        elif safe_engine:
            # No specific model, but has engine: grp-{pool}-{engine}
            parts = [safe_pool, safe_engine]
        else:
            # No engine: grp-{pool}
            parts = [safe_pool]
        return "grp-" + "-".join(p for p in parts if p)
    else:
        # Singleton node (no pool)
        if safe_model and model != "__default__":
            # Has specific model: grp-{engine}-{model}
            parts = [safe_engine, safe_model]
            return "grp-" + "-".join(p for p in parts if p)
        else:
            # No specific model → generic singleton cluster
            return "inferia-workers"

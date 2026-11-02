"""Trusted request metadata for the llm-d data plane.

The public inference API deliberately does not proxy request headers upstream.
In particular, clients must not be able to select an llm-d objective, fairness
flow, or destination by supplying an ``x-inferia-*``, ``x-llm-d-*`` or
``x-gateway-*`` header.  This module is the only place the edge creates those
headers for an llm-d deployment.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any, Dict, Optional


LLMD_SIM_ENGINE = "llm-d-inference-sim"
_RESERVED_PREFIXES = ("x-inferia-", "x-llm-d-", "x-gateway-")


def is_llmd_deployment(deployment: Mapping[str, Any]) -> bool:
    """Whether this deployment is served by the currently supported llm-d mode."""
    return deployment.get("engine") == LLMD_SIM_ENGINE


def trusted_llmd_headers(
    deployment: Mapping[str, Any],
    org_id: Optional[str],
    client_headers: Mapping[str, str],
) -> Dict[str, str]:
    """Build llm-d headers from server-owned state only.

    ``client_headers`` is intentionally accepted so callers make the trust
    boundary explicit.  Reserved client headers are never copied.  Workload
    objectives are not emitted yet: that requires the per-class
    ``InferenceObjective`` resources from WS6.  Fairness is valid without one.
    """
    if not is_llmd_deployment(deployment):
        return {}

    # Read none of the values: keeping this loop makes the deny-list contract
    # executable and protects future additions that forward ordinary headers.
    _ = {
        key: value
        for key, value in client_headers.items()
        if not key.lower().startswith(_RESERVED_PREFIXES)
    }

    headers = {
        "x-inferia-request-id": str(uuid.uuid4()),
        "x-inferia-workload-class": "interactive_chat",
        "x-inferia-priority": "80",
    }
    if org_id:
        headers["x-llm-d-inference-fairness-id"] = str(org_id)
    return headers

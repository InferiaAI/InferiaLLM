"""Resolve a HuggingFace token by name from the persisted providers config,
with legacy-token + INFERIA_HF_TOKEN env fallbacks (priority: active named →
legacy → env). Used by the deploy path to inject HF_TOKEN for the worker.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)


def _load_providers_dict() -> dict:
    """Return the in-memory providers config dict kept live by the api_gateway
    config_manager poller (same object the token-names endpoint reads). Pure
    sync / main-thread-safe — resolve_hf_token runs inside the async /deploy
    handler, where asyncio.run() raises and asyncpg sessions are loop-bound."""
    try:
        from inferia.services.api_gateway.config import settings
        return settings.providers.model_dump()
    except Exception:  # noqa: BLE001
        return {}


def resolve_hf_token(name: Optional[str]) -> Optional[str]:
    """Return the HuggingFace token value for *name*.

    Resolution order:
    1. If *name* is given, find the first entry in ``huggingface.tokens`` whose
       ``name`` matches *name*, ``is_active`` is True (default True), and
       ``token`` is non-empty.
    2. Fall back to the legacy scalar ``huggingface.token``.
    3. Fall back to the ``INFERIA_HF_TOKEN`` environment variable.
    4. Return ``None`` if all three sources are absent.

    Failures in ``_load_providers_dict`` (DB unreachable, etc.) are swallowed;
    the function proceeds directly to env fallback.
    """
    try:
        providers = _load_providers_dict()
    except Exception:  # noqa: BLE001
        providers = {}

    hf = (providers.get("huggingface") or {}) if isinstance(providers, dict) else {}
    tokens = hf.get("tokens") or []

    if name:
        for ent in tokens:
            if not isinstance(ent, dict):
                continue
            if ent.get("name") == name and ent.get("is_active", True):
                tok = ent.get("token")
                if tok:
                    return tok

    legacy = hf.get("token")
    if legacy:
        return legacy

    return os.environ.get("INFERIA_HF_TOKEN") or None

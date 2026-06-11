"""Resolve a HuggingFace token by name from the authoritative DB-decrypted
providers config, with legacy-token + INFERIA_HF_TOKEN env fallbacks.

Priority: active named → legacy huggingface.token → INFERIA_HF_TOKEN env.

Async: reads the DB on the event loop via load_providers_config (the same
accessor used by the AWS provisioning path). The in-memory settings.providers
copy is NOT kept live in the orchestration call path and must not be used here.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)


async def _load_hf_config():
    """Return the decrypted providers.huggingface config object, best-effort
    (returns an empty HuggingFaceConfig on failure). Extracted as a
    module-level function so tests can monkeypatch it with an AsyncMock."""
    try:
        from providers.pulumi.pulumi_aws_adapter import (
            load_providers_config,
        )
        cfg = await load_providers_config()
        return cfg.huggingface
    except Exception:  # noqa: BLE001 — best-effort
        # Return a minimal object with empty fields so the caller never crashes.
        try:
            from api_gateway.config import HuggingFaceConfig
            return HuggingFaceConfig()
        except Exception:  # noqa: BLE001
            return None


async def resolve_hf_token(name: Optional[str]) -> Optional[str]:
    """Return the HuggingFace token value for *name*.

    Resolution order:
    1. If *name* is given, find the first entry in ``huggingface.tokens`` whose
       ``name`` matches *name*, ``is_active`` is True (default True), and
       ``token`` is non-empty.
    2. Fall back to the legacy scalar ``huggingface.token``.
    3. Fall back to the ``INFERIA_HF_TOKEN`` environment variable.
    4. Return ``None`` if all three sources are absent.

    Reads the DB-decrypted providers config via load_providers_config (async,
    on the loop). The in-memory settings.providers copy is stale in the
    orchestration call path and is not used.

    Failures in ``_load_hf_config`` (DB unreachable, etc.) are swallowed;
    the function proceeds directly to env fallback.
    """
    try:
        hf = await _load_hf_config()
    except Exception:  # noqa: BLE001
        hf = None

    if hf is not None:
        # Support both Pydantic-object form (attribute access) and dict form
        # (defensively, in case a test monkeypatches with a plain dict).
        tokens = (hf.get("tokens") if isinstance(hf, dict) else getattr(hf, "tokens", None)) or []
        legacy = (hf.get("token") if isinstance(hf, dict) else getattr(hf, "token", None)) or ""
    else:
        tokens = []
        legacy = ""

    if name:
        for ent in tokens:
            ename = ent.get("name") if isinstance(ent, dict) else getattr(ent, "name", None)
            eact = ent.get("is_active", True) if isinstance(ent, dict) else getattr(ent, "is_active", True)
            etok = ent.get("token") if isinstance(ent, dict) else getattr(ent, "token", None)
            if ename == name and eact and etok:
                return etok

    if legacy:
        return legacy

    return os.environ.get("INFERIA_HF_TOKEN") or None

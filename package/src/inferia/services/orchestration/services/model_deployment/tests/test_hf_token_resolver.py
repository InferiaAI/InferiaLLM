"""Tests for hf_token_resolver — resolve a HF token name → value.

Priority: active named token → legacy huggingface.token → INFERIA_HF_TOKEN env.

All tests are async because resolve_hf_token is now async (reads the DB via
load_providers_config on the event loop). _load_hf_config is monkeypatched
with an AsyncMock returning the huggingface config (object or dict).
"""
from __future__ import annotations

import asyncio
import pytest
from unittest.mock import AsyncMock

from inferia.services.orchestration.services.model_deployment import hf_token_resolver as r

pytestmark = pytest.mark.asyncio


def _hf_cfg(token="", tokens=None):
    """Build a minimal HuggingFaceConfig-like dict for monkeypatching."""
    return {"token": token, "tokens": tokens or []}


# ---------------------------------------------------------------------------
# Core priority tests
# ---------------------------------------------------------------------------


async def test_resolve_named(monkeypatch):
    cfg = _hf_cfg(token="hf_legacy", tokens=[{"name": "prod", "token": "hf_prod", "is_active": True}])
    monkeypatch.setattr(r, "_load_hf_config", AsyncMock(return_value=cfg))
    monkeypatch.setattr(r.os, "environ", {})
    assert await r.resolve_hf_token("prod") == "hf_prod"


async def test_resolve_inactive_skipped_falls_back_legacy(monkeypatch):
    cfg = _hf_cfg(token="hf_legacy", tokens=[{"name": "prod", "token": "hf_prod", "is_active": False}])
    monkeypatch.setattr(r, "_load_hf_config", AsyncMock(return_value=cfg))
    monkeypatch.setattr(r.os, "environ", {})
    assert await r.resolve_hf_token("prod") == "hf_legacy"


async def test_resolve_none_uses_legacy_then_env(monkeypatch):
    monkeypatch.setattr(r, "_load_hf_config", AsyncMock(return_value=_hf_cfg(token="", tokens=[])))
    monkeypatch.setattr(r.os, "environ", {"INFERIA_HF_TOKEN": "hf_env"})
    assert await r.resolve_hf_token(None) == "hf_env"


async def test_resolve_unknown_name_falls_back(monkeypatch):
    monkeypatch.setattr(r, "_load_hf_config", AsyncMock(return_value=_hf_cfg(token="hf_legacy", tokens=[])))
    monkeypatch.setattr(r.os, "environ", {})
    assert await r.resolve_hf_token("nope") == "hf_legacy"


async def test_resolve_first_active_match_when_duplicate_names(monkeypatch):
    cfg = _hf_cfg(tokens=[
        {"name": "d", "token": "hf_1", "is_active": True},
        {"name": "d", "token": "hf_2", "is_active": True},
    ])
    monkeypatch.setattr(r, "_load_hf_config", AsyncMock(return_value=cfg))
    monkeypatch.setattr(r.os, "environ", {})
    assert await r.resolve_hf_token("d") == "hf_1"  # first match wins


# ---------------------------------------------------------------------------
# Edge-case coverage
# ---------------------------------------------------------------------------


async def test_resolve_no_providers_at_all_returns_none(monkeypatch):
    """Empty providers dict + no env → None."""
    monkeypatch.setattr(r, "_load_hf_config", AsyncMock(return_value=_hf_cfg()))
    monkeypatch.setattr(r.os, "environ", {})
    assert await r.resolve_hf_token(None) is None


async def test_resolve_none_name_with_active_legacy(monkeypatch):
    """name=None skips the tokens list and goes straight to legacy."""
    cfg = _hf_cfg(token="hf_leg", tokens=[{"name": "prod", "token": "hf_prod", "is_active": True}])
    monkeypatch.setattr(r, "_load_hf_config", AsyncMock(return_value=cfg))
    monkeypatch.setattr(r.os, "environ", {})
    assert await r.resolve_hf_token(None) == "hf_leg"


async def test_resolve_named_no_legacy_no_env_returns_none(monkeypatch):
    """Named token not found, no legacy, no env → None."""
    monkeypatch.setattr(r, "_load_hf_config", AsyncMock(return_value=_hf_cfg(token="", tokens=[])))
    monkeypatch.setattr(r.os, "environ", {})
    assert await r.resolve_hf_token("missing") is None


async def test_resolve_load_failure_falls_back_to_env(monkeypatch):
    """If _load_hf_config raises, resolve_hf_token must not propagate."""
    async def _boom():
        raise RuntimeError("db unreachable")
    monkeypatch.setattr(r, "_load_hf_config", _boom)
    monkeypatch.setattr(r.os, "environ", {"INFERIA_HF_TOKEN": "hf_fallback"})
    assert await r.resolve_hf_token("any") == "hf_fallback"


async def test_resolve_empty_token_string_not_returned(monkeypatch):
    """An active entry with token='' is treated as missing."""
    cfg = _hf_cfg(token="hf_leg", tokens=[{"name": "x", "token": "", "is_active": True}])
    monkeypatch.setattr(r, "_load_hf_config", AsyncMock(return_value=cfg))
    monkeypatch.setattr(r.os, "environ", {})
    assert await r.resolve_hf_token("x") == "hf_leg"


async def test_resolve_env_not_used_when_legacy_present(monkeypatch):
    """Legacy token takes priority over INFERIA_HF_TOKEN."""
    monkeypatch.setattr(r, "_load_hf_config", AsyncMock(return_value=_hf_cfg(token="hf_legacy")))
    monkeypatch.setattr(r.os, "environ", {"INFERIA_HF_TOKEN": "hf_env"})
    assert await r.resolve_hf_token(None) == "hf_legacy"


async def test_resolve_hf_config_returns_none_falls_back_to_env(monkeypatch):
    """_load_hf_config returning None must not crash (uses env fallback)."""
    monkeypatch.setattr(r, "_load_hf_config", AsyncMock(return_value=None))
    monkeypatch.setattr(r.os, "environ", {"INFERIA_HF_TOKEN": "hf_env2"})
    assert await r.resolve_hf_token("x") == "hf_env2"


async def test_resolve_pydantic_object_form(monkeypatch):
    """resolve_hf_token works when _load_hf_config returns a Pydantic object (not dict)."""
    from inferia.services.api_gateway.config import HuggingFaceConfig, HFTokenEntry
    hf = HuggingFaceConfig(
        token="hf_legacy_pydantic",
        tokens=[HFTokenEntry(name="p", token="hf_pydantic", is_active=True)],
    )
    monkeypatch.setattr(r, "_load_hf_config", AsyncMock(return_value=hf))
    monkeypatch.setattr(r.os, "environ", {})
    assert await r.resolve_hf_token("p") == "hf_pydantic"
    assert await r.resolve_hf_token(None) == "hf_legacy_pydantic"


async def test_resolve_pydantic_inactive_falls_back(monkeypatch):
    """Inactive Pydantic HFTokenEntry falls back to legacy token."""
    from inferia.services.api_gateway.config import HuggingFaceConfig, HFTokenEntry
    hf = HuggingFaceConfig(
        token="hf_leg_pb",
        tokens=[HFTokenEntry(name="p", token="hf_pb", is_active=False)],
    )
    monkeypatch.setattr(r, "_load_hf_config", AsyncMock(return_value=hf))
    monkeypatch.setattr(r.os, "environ", {})
    assert await r.resolve_hf_token("p") == "hf_leg_pb"


# ---------------------------------------------------------------------------
# Loop-safety: _load_hf_config is awaitable + returns object-or-dict
# (smoke-test only — no live DB needed in inferia-test)
# ---------------------------------------------------------------------------


async def test_load_hf_config_is_awaitable_and_returns_object_or_none():
    """The real _load_hf_config must be awaitable and return a HuggingFaceConfig
    object (or None / HuggingFaceConfig() on DB failure) without raising.
    Runs inside an async test to confirm loop-safety."""
    result = await r._load_hf_config()
    # In the test environment there may be no DB, so result is either a
    # HuggingFaceConfig or None.  Either is fine — just must not raise.
    from inferia.services.api_gateway.config import HuggingFaceConfig
    assert result is None or isinstance(result, HuggingFaceConfig), (
        f"_load_hf_config returned unexpected type: {type(result)!r}"
    )

"""Tests for hf_token_resolver — resolve a HF token name → value.

Priority: active named token → legacy huggingface.token → INFERIA_HF_TOKEN env.
"""
from inferia.services.orchestration.services.model_deployment import hf_token_resolver as r


def test_resolve_named(monkeypatch):
    cfg = {"huggingface": {"token": "hf_legacy", "tokens": [{"name": "prod", "token": "hf_prod", "is_active": True}]}}
    monkeypatch.setattr(r, "_load_providers_dict", lambda: cfg)
    monkeypatch.setattr(r.os, "environ", {})
    assert r.resolve_hf_token("prod") == "hf_prod"


def test_resolve_inactive_skipped_falls_back_legacy(monkeypatch):
    cfg = {"huggingface": {"token": "hf_legacy", "tokens": [{"name": "prod", "token": "hf_prod", "is_active": False}]}}
    monkeypatch.setattr(r, "_load_providers_dict", lambda: cfg)
    monkeypatch.setattr(r.os, "environ", {})
    assert r.resolve_hf_token("prod") == "hf_legacy"


def test_resolve_none_uses_legacy_then_env(monkeypatch):
    monkeypatch.setattr(r, "_load_providers_dict", lambda: {"huggingface": {"token": "", "tokens": []}})
    monkeypatch.setattr(r.os, "environ", {"INFERIA_HF_TOKEN": "hf_env"})
    assert r.resolve_hf_token(None) == "hf_env"


def test_resolve_unknown_name_falls_back(monkeypatch):
    monkeypatch.setattr(r, "_load_providers_dict", lambda: {"huggingface": {"token": "hf_legacy", "tokens": []}})
    monkeypatch.setattr(r.os, "environ", {})
    assert r.resolve_hf_token("nope") == "hf_legacy"


def test_resolve_first_active_match_when_duplicate_names(monkeypatch):
    cfg = {"huggingface": {"tokens": [{"name": "d", "token": "hf_1", "is_active": True}, {"name": "d", "token": "hf_2", "is_active": True}]}}
    monkeypatch.setattr(r, "_load_providers_dict", lambda: cfg)
    monkeypatch.setattr(r.os, "environ", {})
    assert r.resolve_hf_token("d") == "hf_1"  # first match wins


# ---------- extra edge-case coverage ------------------------------------


def test_resolve_no_providers_at_all_returns_none(monkeypatch):
    """Empty providers dict + no env → None."""
    monkeypatch.setattr(r, "_load_providers_dict", lambda: {})
    monkeypatch.setattr(r.os, "environ", {})
    assert r.resolve_hf_token(None) is None


def test_resolve_none_name_with_active_legacy(monkeypatch):
    """name=None skips the tokens list and goes straight to legacy."""
    cfg = {"huggingface": {"token": "hf_leg", "tokens": [{"name": "prod", "token": "hf_prod", "is_active": True}]}}
    monkeypatch.setattr(r, "_load_providers_dict", lambda: cfg)
    monkeypatch.setattr(r.os, "environ", {})
    assert r.resolve_hf_token(None) == "hf_leg"


def test_resolve_named_no_legacy_no_env_returns_none(monkeypatch):
    """Named token not found, no legacy, no env → None."""
    cfg = {"huggingface": {"token": "", "tokens": []}}
    monkeypatch.setattr(r, "_load_providers_dict", lambda: cfg)
    monkeypatch.setattr(r.os, "environ", {})
    assert r.resolve_hf_token("missing") is None


def test_resolve_load_failure_falls_back_to_env(monkeypatch):
    """If _load_providers_dict raises, resolve_hf_token must not propagate."""
    def _boom():
        raise RuntimeError("db unreachable")
    monkeypatch.setattr(r, "_load_providers_dict", _boom)
    monkeypatch.setattr(r.os, "environ", {"INFERIA_HF_TOKEN": "hf_fallback"})
    assert r.resolve_hf_token("any") == "hf_fallback"


def test_resolve_empty_token_string_not_returned(monkeypatch):
    """An active entry with token='' is treated as missing."""
    cfg = {"huggingface": {"token": "hf_leg", "tokens": [{"name": "x", "token": "", "is_active": True}]}}
    monkeypatch.setattr(r, "_load_providers_dict", lambda: cfg)
    monkeypatch.setattr(r.os, "environ", {})
    assert r.resolve_hf_token("x") == "hf_leg"


def test_resolve_env_not_used_when_legacy_present(monkeypatch):
    """Legacy token takes priority over INFERIA_HF_TOKEN."""
    cfg = {"huggingface": {"token": "hf_legacy", "tokens": []}}
    monkeypatch.setattr(r, "_load_providers_dict", lambda: cfg)
    monkeypatch.setattr(r.os, "environ", {"INFERIA_HF_TOKEN": "hf_env"})
    assert r.resolve_hf_token(None) == "hf_legacy"


def test_resolve_malformed_providers_dict(monkeypatch):
    """Non-dict return from _load_providers_dict must not crash."""
    monkeypatch.setattr(r, "_load_providers_dict", lambda: None)
    monkeypatch.setattr(r.os, "environ", {})
    assert r.resolve_hf_token("x") is None


def test_load_providers_dict_safe_in_running_loop():
    # The real _load_providers_dict must be callable from within a running loop
    # (resolve_hf_token runs inside the async /deploy handler) without raising.
    import asyncio as _aio

    async def _main():
        return r._load_providers_dict()

    out = _aio.run(_main())
    assert isinstance(out, dict)  # returns a dict, no RuntimeError

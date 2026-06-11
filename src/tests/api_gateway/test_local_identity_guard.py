"""Tests for the require_local_identity dependency.

Run with --noconftest to avoid the jwt import conflict in conftest.py:
    python -m pytest src/services/api_gateway/tests/test_local_identity_guard.py -v --noconftest
"""
import pytest
from fastapi import FastAPI, Depends
from fastapi.testclient import TestClient
import services.api_gateway.rbac.local_identity_guard as guard


def _app():
    app = FastAPI()

    @app.get("/x", dependencies=[Depends(guard.require_local_identity)])
    def x():
        return {"ok": True}

    return app


# ---------------------------------------------------------------------------
# Dependency unit tests
# ---------------------------------------------------------------------------


def test_blocks_in_external_mode(monkeypatch):
    monkeypatch.setattr(guard.settings, "auth_provider", "inferiaauth", raising=False)
    r = TestClient(_app()).get("/x")
    assert r.status_code == 409


def test_blocks_in_oidc_mode(monkeypatch):
    monkeypatch.setattr(guard.settings, "auth_provider", "oidc", raising=False)
    assert TestClient(_app()).get("/x").status_code == 409


def test_allows_in_local_mode(monkeypatch):
    monkeypatch.setattr(guard.settings, "auth_provider", "local", raising=False)
    assert TestClient(_app()).get("/x").status_code == 200


# ---------------------------------------------------------------------------
# Error body tests
# ---------------------------------------------------------------------------


def test_conflict_detail_message(monkeypatch):
    monkeypatch.setattr(guard.settings, "auth_provider", "inferiaauth", raising=False)
    r = TestClient(_app()).get("/x")
    assert r.status_code == 409
    body = r.json()
    assert "identity provider" in body["detail"]


def test_conflict_detail_message_oidc(monkeypatch):
    monkeypatch.setattr(guard.settings, "auth_provider", "oidc", raising=False)
    r = TestClient(_app()).get("/x")
    assert r.status_code == 409
    body = r.json()
    assert "identity provider" in body["detail"]


# ---------------------------------------------------------------------------
# Direct function call tests
# ---------------------------------------------------------------------------


def test_direct_call_raises_in_external_mode(monkeypatch):
    """Calling require_local_identity() directly raises HTTPException when external."""
    from fastapi import HTTPException

    monkeypatch.setattr(guard.settings, "auth_provider", "inferiaauth", raising=False)
    with pytest.raises(HTTPException) as exc_info:
        guard.require_local_identity()
    assert exc_info.value.status_code == 409


def test_direct_call_raises_in_oidc_mode(monkeypatch):
    from fastapi import HTTPException

    monkeypatch.setattr(guard.settings, "auth_provider", "oidc", raising=False)
    with pytest.raises(HTTPException) as exc_info:
        guard.require_local_identity()
    assert exc_info.value.status_code == 409


def test_direct_call_noop_in_local_mode(monkeypatch):
    """Calling require_local_identity() directly does NOT raise when local."""
    monkeypatch.setattr(guard.settings, "auth_provider", "local", raising=False)
    # Should not raise
    result = guard.require_local_identity()
    assert result is None


# ---------------------------------------------------------------------------
# is_external_mode property derives from auth_provider
# ---------------------------------------------------------------------------


def test_is_external_mode_true_for_inferiaauth(monkeypatch):
    monkeypatch.setattr(guard.settings, "auth_provider", "inferiaauth", raising=False)
    assert guard.settings.is_external_mode is True


def test_is_external_mode_true_for_oidc(monkeypatch):
    monkeypatch.setattr(guard.settings, "auth_provider", "oidc", raising=False)
    assert guard.settings.is_external_mode is True


def test_is_external_mode_false_for_local(monkeypatch):
    monkeypatch.setattr(guard.settings, "auth_provider", "local", raising=False)
    assert guard.settings.is_external_mode is False

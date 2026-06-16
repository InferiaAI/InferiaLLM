from __future__ import annotations

"""Unit tests for _provisioning_route — the capability-based provider router.

Tests use the REAL adapter registry (no mocks) so the capability matrix is
exercised as it stands in production. This means importing deployment_server
must succeed; if heavy side-effects block the import, report rather than hack.
"""
import pytest
from orchestration.models.model_deployment.deployment_server import _provisioning_route


# ---------------------------------------------------------------------------
# Happy-path: every registered provider routes to the correct bucket
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "provider,expected",
    [
        ("aws", "reconciler"),
        ("gcp", "reconciler"),
        ("azure", "reconciler"),
        ("nosana", "direct_adapter"),
        ("akash", "direct_adapter"),
        ("k8s", "direct_adapter"),
        ("worker", "self_register"),
        ("on_prem", "self_register"),
    ],
)
def test_route_by_provider(provider, expected):
    assert _provisioning_route(provider, {}) == expected


# ---------------------------------------------------------------------------
# pool_meta agent_kind override
# ---------------------------------------------------------------------------
def test_worker_pool_overrides_to_self_register():
    """agent_kind=worker must short-circuit even for a cloud provider."""
    assert _provisioning_route("aws", {"agent_kind": "worker"}) == "self_register"


# ---------------------------------------------------------------------------
# Unknown provider fallback
# ---------------------------------------------------------------------------
def test_unknown_provider_defaults_to_reconciler():
    """An unregistered provider string must not crash — fall back to reconciler."""
    assert _provisioning_route("does-not-exist", {}) == "reconciler"


# ---------------------------------------------------------------------------
# None pool_meta hardening
# ---------------------------------------------------------------------------
def test_none_pool_meta_treated_as_empty():
    """None pool_meta must not raise — treated as an empty dict (reconciler for aws)."""
    assert _provisioning_route("aws", None) == "reconciler"


# ---------------------------------------------------------------------------
# Empty-string provider fallback
# ---------------------------------------------------------------------------
def test_empty_string_provider_defaults_to_reconciler():
    """An empty-string provider is not in ADAPTER_REGISTRY — must fall back to reconciler."""
    assert _provisioning_route("", {}) == "reconciler"


# ---------------------------------------------------------------------------
# Rule 1 short-circuits even non-cloud providers
# ---------------------------------------------------------------------------
def test_agent_kind_overrides_depin_provider():
    """agent_kind=worker must short-circuit to self_register even for a DePIN provider."""
    assert _provisioning_route("nosana", {"agent_kind": "worker"}) == "self_register"

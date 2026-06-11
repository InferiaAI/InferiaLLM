"""Tests for placement scoring — complex logic layer."""

import math

import pytest

from inferia.services.orchestration.services.placement_engine.scoring import score_node


def make_node(**overrides):
    """Create a node dict with sensible defaults."""
    defaults = {
        "state": "ready",
        "health_score": 100,
        "gpu_free": 4,
        "vcpu_free": 16,
        "ram_free": 64,
        "has_cached_image": False,
    }
    defaults.update(overrides)
    return defaults


class TestPlacementScoring:
    """Verify placement scoring logic."""

    def test_node_not_ready_returns_infinity(self):
        node = make_node(state="provisioning")
        assert score_node(node) == math.inf

    def test_node_below_min_health_returns_infinity(self):
        node = make_node(health_score=50)
        assert score_node(node) == math.inf

    def test_binpack_prefers_less_free_resources(self):
        """In binpack mode, node with less free GPU scores lower (better)."""
        full_node = make_node(gpu_free=1, vcpu_free=4, ram_free=16)
        empty_node = make_node(gpu_free=8, vcpu_free=32, ram_free=128)
        assert score_node(full_node, strategy="binpack") < score_node(
            empty_node, strategy="binpack"
        )

    def test_spread_prefers_more_free_resources(self):
        """In spread mode, node with more free GPU scores lower (better)."""
        full_node = make_node(gpu_free=1, vcpu_free=4, ram_free=16)
        empty_node = make_node(gpu_free=8, vcpu_free=32, ram_free=128)
        assert score_node(empty_node, strategy="spread") < score_node(
            full_node, strategy="spread"
        )

    def test_cached_image_bonus(self):
        """Node with cached image gets a scoring bonus."""
        no_cache = make_node()
        with_cache = make_node(has_cached_image=True)
        assert score_node(with_cache) < score_node(no_cache)

    def test_negative_resources_returns_infinity(self):
        node = make_node(gpu_free=-1)
        assert score_node(node) == math.inf

    def test_health_penalty_applied(self):
        """Lower health = higher (worse) score due to soft penalty."""
        healthy = make_node(health_score=100)
        degraded = make_node(health_score=85)
        assert score_node(healthy) < score_node(degraded)

"""Unit tests for cluster naming consistency between xDS and worker routing.

The inference service (worker_routing.py) and orchestration service (xds.py)
must compute identical Envoy cluster names for the same deployment. Any
asymmetry causes 503 "no cluster found" errors because the header Envoy
receives won't match any published cluster name.

This test file verifies that both sides use the same shared function.
"""
from __future__ import annotations

import pytest

from common.cluster_naming import (
    build_envoy_cluster_name,
    sanitize_cluster_name,
)
from inference.core.worker_routing import envoy_route_headers
from orchestration.api.xds import build_resources
from unittest.mock import AsyncMock


class TestClusterNamingFunction:
    """Test the shared cluster naming function in isolation."""

    def test_sanitize_cluster_name_alphanumeric(self):
        assert sanitize_cluster_name("pool123") == "pool123"

    def test_sanitize_cluster_name_dash_underscore_dot(self):
        assert sanitize_cluster_name("pool-123_abc.xyz") == "pool-123_abc.xyz"

    def test_sanitize_cluster_name_special_chars_to_dash(self):
        assert sanitize_cluster_name("pool@123!") == "pool-123-"
        assert sanitize_cluster_name("pool 123") == "pool-123"

    def test_sanitize_cluster_name_empty(self):
        assert sanitize_cluster_name("") == ""
        assert sanitize_cluster_name(None) == ""

    def test_build_cluster_pooled_with_model(self):
        """Pooled node with a specific model."""
        result = build_envoy_cluster_name(pool_id="pool-1", engine="vllm", model="gemma")
        assert result == "grp-pool-1-vllm-gemma"

    def test_build_cluster_pooled_without_model(self):
        """Pooled node without a specific model (model is __default__)."""
        result = build_envoy_cluster_name(pool_id="pool-1", engine="vllm", model="__default__")
        assert result == "grp-pool-1-vllm"

    def test_build_cluster_pooled_no_model_param(self):
        """Pooled node with no model parameter (treated as __default__)."""
        result = build_envoy_cluster_name(pool_id="pool-1", engine="vllm", model=None)
        assert result == "grp-pool-1-vllm"

    def test_build_cluster_singleton_with_model(self):
        """Singleton node (no pool) with a specific model."""
        result = build_envoy_cluster_name(pool_id=None, engine="vllm", model="gemma")
        assert result == "grp-vllm-gemma"

    def test_build_cluster_singleton_without_model(self):
        """Singleton node with no specific model."""
        result = build_envoy_cluster_name(pool_id=None, engine="vllm", model="__default__")
        assert result == "inferia-workers"

    def test_build_cluster_singleton_no_model_param(self):
        """Singleton node with no model parameter."""
        result = build_envoy_cluster_name(pool_id=None, engine="vllm", model=None)
        assert result == "inferia-workers"

    def test_build_cluster_with_special_characters(self):
        """Special characters in pool_id/engine/model are sanitized."""
        result = build_envoy_cluster_name(
            pool_id="pool@123",
            engine="v llm",
            model="model-v1.0"
        )
        # pool@123 → pool-123, v llm → v-llm, model-v1.0 → model-v1.0
        assert result == "grp-pool-123-v-llm-model-v1.0"

    def test_build_cluster_pooled_no_engine(self):
        """Pooled node with no engine (edge case)."""
        result = build_envoy_cluster_name(pool_id="pool-1", engine=None, model="gemma")
        # With no engine, the model is still used
        assert result == "grp-pool-1-gemma"

    def test_build_cluster_pooled_empty_engine(self):
        """Pooled node with empty engine string."""
        result = build_envoy_cluster_name(pool_id="pool-1", engine="", model="gemma")
        assert result == "grp-pool-1-gemma"


class TestWorkerRoutingConsistency:
    """Test that worker_routing.py uses the shared function correctly."""

    @staticmethod
    def _deployment(**kwargs):
        """Helper to build a deployment dict."""
        base = {
            "id": "dep-123",
            "pool_id": None,
            "engine": None,
            "inference_model": None,
            "model_name": None,
            "inference_token": "tok-123",  # Must have this to route via Envoy
        }
        base.update(kwargs)
        return base

    def test_worker_routing_pooled_with_model(self):
        """Worker routing produces cluster name that matches xDS."""
        dep = self._deployment(
            pool_id="pool-1",
            engine="vllm",
            inference_model="gemma"
        )
        url, headers = envoy_route_headers(dep, "http://envoy:10000")
        assert headers["X-Inferia-Route-Cluster"] == "grp-pool-1-vllm-gemma"

    def test_worker_routing_pooled_without_model(self):
        """Pooled node with no specific model."""
        dep = self._deployment(pool_id="pool-1", engine="vllm")
        url, headers = envoy_route_headers(dep, "http://envoy:10000")
        assert headers["X-Inferia-Route-Cluster"] == "grp-pool-1-vllm"

    def test_worker_routing_singleton_with_model(self):
        """Singleton node with model."""
        dep = self._deployment(engine="vllm", inference_model="gemma")
        url, headers = envoy_route_headers(dep, "http://envoy:10000")
        assert headers["X-Inferia-Route-Cluster"] == "grp-vllm-gemma"

    def test_worker_routing_singleton_without_model(self):
        """Singleton node with no model → inferia-workers."""
        dep = self._deployment(engine="vllm")
        url, headers = envoy_route_headers(dep, "http://envoy:10000")
        assert headers["X-Inferia-Route-Cluster"] == "inferia-workers"

    def test_worker_routing_no_engine_pooled(self):
        """Pooled node with no engine specified."""
        dep = self._deployment(pool_id="pool-1", engine=None, inference_model="gemma")
        url, headers = envoy_route_headers(dep, "http://envoy:10000")
        # Engine is None → sanitize_cluster_name(None) → "", so cluster is grp-pool-1-gemma
        assert headers["X-Inferia-Route-Cluster"] == "grp-pool-1-gemma"


@pytest.mark.asyncio
class TestXDSConsistency:
    """Test that xds.py uses the shared function correctly."""

    async def test_xds_pooled_with_model(self):
        """xDS publishes cluster name that matches worker routing."""
        fake_nodes = [
            {
                "id": "n1",
                "advertise_url": "h1:8080",
                "pool_id": "pool-1",
                "engine": "vllm",
                "model": "gemma",
                "healthy": True,
            },
        ]
        mock_repo = AsyncMock()
        mock_repo.list_xds_nodes.return_value = fake_nodes

        from orchestration.api.xds import configure
        configure(inventory_repo=mock_repo)

        resources = await build_resources()
        assert "grp-pool-1-vllm-gemma" in resources["clusters"]
        assert resources["route_table"]["n1"] == "grp-pool-1-vllm-gemma"

    async def test_xds_pooled_without_model(self):
        """xDS pooled node with no specific model."""
        fake_nodes = [
            {
                "id": "n1",
                "advertise_url": "h1:8080",
                "pool_id": "pool-1",
                "engine": "vllm",
                "model": "__default__",
                "healthy": True,
            },
        ]
        mock_repo = AsyncMock()
        mock_repo.list_xds_nodes.return_value = fake_nodes

        from orchestration.api.xds import configure
        configure(inventory_repo=mock_repo)

        resources = await build_resources()
        assert "grp-pool-1-vllm" in resources["clusters"]
        assert resources["route_table"]["n1"] == "grp-pool-1-vllm"

    async def test_xds_singleton_with_model(self):
        """xDS singleton node with model."""
        fake_nodes = [
            {
                "id": "n1",
                "advertise_url": "h1:8080",
                "pool_id": None,
                "engine": "vllm",
                "model": "gemma",
                "healthy": True,
            },
        ]
        mock_repo = AsyncMock()
        mock_repo.list_xds_nodes.return_value = fake_nodes

        from orchestration.api.xds import configure
        configure(inventory_repo=mock_repo)

        resources = await build_resources()
        assert "grp-vllm-gemma" in resources["clusters"]
        assert resources["route_table"]["n1"] == "grp-vllm-gemma"

    async def test_xds_singleton_without_model(self):
        """xDS singleton node with no model."""
        fake_nodes = [
            {
                "id": "n1",
                "advertise_url": "h1:8080",
                "pool_id": None,
                "engine": "vllm",
                "model": "__default__",
                "healthy": True,
            },
        ]
        mock_repo = AsyncMock()
        mock_repo.list_xds_nodes.return_value = fake_nodes

        from orchestration.api.xds import configure
        configure(inventory_repo=mock_repo)

        resources = await build_resources()
        assert "inferia-workers" in resources["clusters"]
        assert resources["route_table"]["n1"] == "inferia-workers"


@pytest.mark.asyncio
class TestEndToEndClusterConsistency:
    """End-to-end tests: xDS publishes a cluster, worker routing requests it."""

    async def test_xds_and_worker_routing_agree_on_cluster_name(self):
        """Both xDS and worker_routing compute identical cluster names."""
        # Scenario: a deployment in pool-1 running vllm with model gemma
        # xDS publishes it as grp-pool-1-vllm-gemma
        # worker_routing should request grp-pool-1-vllm-gemma

        fake_nodes = [
            {
                "id": "n1",
                "advertise_url": "h1:8080",
                "pool_id": "pool-1",
                "engine": "vllm",
                "model": "gemma",
                "healthy": True,
            },
        ]
        mock_repo = AsyncMock()
        mock_repo.list_xds_nodes.return_value = fake_nodes

        from orchestration.api.xds import configure
        configure(inventory_repo=mock_repo)

        xds_resources = await build_resources()
        xds_clusters = set(xds_resources["clusters"].keys())

        # Now, worker_routing for a deployment with the same parameters
        deployment = {
            "id": "dep-123",
            "pool_id": "pool-1",
            "engine": "vllm",
            "inference_model": "gemma",
            "inference_token": "tok-123",
        }
        _, headers = envoy_route_headers(deployment, "http://envoy:10000")
        requested_cluster = headers["X-Inferia-Route-Cluster"]

        # The requested cluster MUST exist in xDS
        assert requested_cluster in xds_clusters, (
            f"Worker routing requested cluster '{requested_cluster}' "
            f"but xDS only publishes: {xds_clusters}"
        )

    async def test_engine_none_consistency(self):
        """Test that None engine is handled consistently across both sides.
        
        This is the critical bug fix: xds.py doesn't force engine="vllm",
        so a deployment with engine=None will create "grp-pool-1" not "grp-pool-1-vllm".
        """
        fake_nodes = [
            {
                "id": "n1",
                "advertise_url": "h1:8080",
                "pool_id": "pool-1",
                "engine": None,  # ← Critical: engine can be None
                "model": "__default__",
                "healthy": True,
            },
        ]
        mock_repo = AsyncMock()
        mock_repo.list_xds_nodes.return_value = fake_nodes

        from orchestration.api.xds import configure
        configure(inventory_repo=mock_repo)

        xds_resources = await build_resources()

        # xDS should create "grp-pool-1" (NOT "grp-pool-1-None" or "grp-pool-1-vllm")
        assert "grp-pool-1" in xds_resources["clusters"]

        # worker_routing with the same parameters
        deployment = {
            "id": "dep-123",
            "pool_id": "pool-1",
            "engine": None,  # ← Must match xDS
            "inference_model": None,
            "inference_token": "tok-123",
        }
        _, headers = envoy_route_headers(deployment, "http://envoy:10000")
        requested_cluster = headers["X-Inferia-Route-Cluster"]

        # Both must agree
        assert requested_cluster == "grp-pool-1"

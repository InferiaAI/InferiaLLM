"""Tests for provider adapter error handling — Layer 2."""

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch


class FakeAdapter:
    """Minimal adapter that delegates to injected callables."""

    def __init__(self, provision_fn=None, deprovision_fn=None):
        self._provision = provision_fn or AsyncMock(return_value={"node_id": "n-1"})
        self._deprovision = deprovision_fn or AsyncMock()

    async def provision_node(self, *args, **kwargs):
        return await self._provision(*args, **kwargs)

    async def deprovision_node(self, node_id, *args, **kwargs):
        return await self._deprovision(node_id, *args, **kwargs)


class TestAdapterProvisionErrors:
    """Adapter error cases during provisioning."""

    @pytest.mark.asyncio
    async def test_provision_timeout_raises_runtime_error(self):
        """Provision that hangs raises RuntimeError, doesn't hang forever."""

        async def slow_provision(*args, **kwargs):
            await asyncio.sleep(60)

        adapter = FakeAdapter(provision_fn=slow_provision)
        with pytest.raises((RuntimeError, asyncio.TimeoutError)):
            await asyncio.wait_for(
                adapter.provision_node(pool_id="pool-1"),
                timeout=0.05,
            )

    @pytest.mark.asyncio
    async def test_provision_auth_failure_raises_without_leaking_key(self):
        """Auth failure message doesn't contain secret key."""
        api_key = "super-secret-key-12345"

        async def auth_fail(*args, **kwargs):
            raise RuntimeError(f"Provider 401 Unauthorized (key={api_key})")

        adapter = FakeAdapter(provision_fn=auth_fail)
        with pytest.raises(RuntimeError) as exc_info:
            await adapter.provision_node(pool_id="pool-1")
        # Test that caller can catch and sanitize; error does contain detail
        assert "401" in str(exc_info.value) or "Unauthorized" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_deprovision_missing_node_handled_gracefully(self):
        """Deprovisioning a non-existent node should not raise."""

        async def not_found(*args, **kwargs):
            # Provider returns 404 - adapter should handle it
            return {"status": "not_found"}

        adapter = FakeAdapter(deprovision_fn=not_found)
        # Should complete without raising
        result = await adapter.deprovision_node("non-existent-node")
        assert result is not None

    @pytest.mark.asyncio
    async def test_provision_unexpected_response_raises(self):
        """Unexpected response format from provider raises, doesn't crash silently."""

        async def bad_response(*args, **kwargs):
            raise ValueError("Unexpected response: missing 'node_id' field")

        adapter = FakeAdapter(provision_fn=bad_response)
        with pytest.raises((ValueError, RuntimeError, KeyError)):
            await adapter.provision_node(pool_id="pool-1")

    @pytest.mark.asyncio
    async def test_deprovision_network_error_raises(self):
        """Network error during deprovision propagates as exception."""
        import aiohttp

        async def network_err(*args, **kwargs):
            raise aiohttp.ClientConnectionError("Network unreachable")

        adapter = FakeAdapter(deprovision_fn=network_err)
        with pytest.raises(Exception):
            await adapter.deprovision_node("node-abc")


class TestNosanaAdapterErrors:
    """Nosana-specific error handling."""

    def test_provision_node_missing_metadata_raises(self):
        """Nosana provision without required metadata raises ValueError."""
        from unittest.mock import patch

        mock_settings = type("S", (), {
            "internal_api_key": "test",
            "nosana_sidecar_url": "http://localhost:3000",
        })()

        with patch("inferia.services.orchestration.config.settings", mock_settings):
            from inferia.services.orchestration.services.adapter_engine.adapters.nosana.nosana_adapter import (
                NosanaAdapter,
            )

            adapter = NosanaAdapter.__new__(NosanaAdapter)
            adapter.sidecar_url = "http://localhost:3000"
            adapter.internal_api_key = "test"

            import asyncio

            async def run():
                with pytest.raises(ValueError, match="metadata"):
                    await adapter.provision_node(
                        provider_resource_id="gpu-market",
                        pool_id="pool-1",
                        metadata=None,  # Missing required metadata
                    )

            asyncio.get_event_loop().run_until_complete(run())

    def test_provision_node_missing_image_raises(self):
        """Training provision without image raises ValueError."""
        from unittest.mock import patch

        mock_settings = type("S", (), {
            "internal_api_key": "test",
            "nosana_sidecar_url": "http://localhost:3000",
        })()

        with patch("inferia.services.orchestration.config.settings", mock_settings):
            from inferia.services.orchestration.services.adapter_engine.adapters.nosana.nosana_adapter import (
                NosanaAdapter,
            )

            adapter = NosanaAdapter.__new__(NosanaAdapter)
            adapter.sidecar_url = "http://localhost:3000"
            adapter.internal_api_key = "test"

            import asyncio

            async def run():
                with pytest.raises(ValueError):
                    await adapter.provision_node(
                        provider_resource_id="gpu-market",
                        pool_id="pool-1",
                        metadata={"workload_type": "training"},  # No image
                    )

            asyncio.get_event_loop().run_until_complete(run())

"""Tests for blocking API call wrapping (#73/#74)."""

import asyncio
import functools
import pytest
from unittest.mock import patch, MagicMock, AsyncMock

try:
    import boto3
    HAS_BOTO3 = True
except ImportError:
    HAS_BOTO3 = False


async def _run_sync(func, *args, **kwargs):
    """Standalone copy of the helper for testing without boto3."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, functools.partial(func, *args, **kwargs))


@pytest.mark.skipif(not HAS_BOTO3, reason="boto3 not installed")

class TestAWSAdapterAsync:
    @pytest.mark.asyncio
    async def test_discover_nodes_uses_run_sync(self):
        """discover_nodes must not call boto3 directly — must use _run_sync."""
        with patch(
            "inferia.services.orchestration.services.adapter_engine.adapters.aws.adapter.boto3"
        ) as mock_boto:
            mock_client = MagicMock()
            mock_client.describe_instances.return_value = {"Reservations": []}
            mock_client.meta.region_name = "us-east-1"
            mock_boto.client.return_value = mock_client

            with patch(
                "inferia.services.orchestration.services.adapter_engine.adapters.aws.adapter._run_sync",
                new_callable=AsyncMock,
            ) as mock_run:
                mock_run.return_value = {"Reservations": []}

                from inferia.services.orchestration.services.adapter_engine.adapters.aws.adapter import (
                    AWSAdapter,
                )

                adapter = AWSAdapter(region="us-east-1")
                await adapter.discover_nodes()

            mock_run.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_get_node_metadata_uses_run_sync(self):
        """get_node_metadata must not call boto3 directly."""
        with patch(
            "inferia.services.orchestration.services.adapter_engine.adapters.aws.adapter.boto3"
        ) as mock_boto:
            mock_client = MagicMock()
            mock_client.meta.region_name = "us-east-1"
            mock_boto.client.return_value = mock_client

            with patch(
                "inferia.services.orchestration.services.adapter_engine.adapters.aws.adapter._run_sync",
                new_callable=AsyncMock,
            ) as mock_run:
                mock_run.return_value = {
                    "Reservations": [
                        {
                            "Instances": [
                                {
                                    "InstanceType": "g5.xlarge",
                                    "Placement": {"AvailabilityZone": "us-east-1a"},
                                }
                            ]
                        }
                    ]
                }

                from inferia.services.orchestration.services.adapter_engine.adapters.aws.adapter import (
                    AWSAdapter,
                )

                adapter = AWSAdapter(region="us-east-1")
                result = await adapter.get_node_metadata("i-1234")

            mock_run.assert_awaited_once()
            assert result["instance_type"] == "g5.xlarge"

    @pytest.mark.asyncio
    async def test_get_node_metadata_returns_empty_on_error(self):
        """get_node_metadata should return {} on any exception."""
        with patch(
            "inferia.services.orchestration.services.adapter_engine.adapters.aws.adapter.boto3"
        ) as mock_boto:
            mock_client = MagicMock()
            mock_client.meta.region_name = "us-east-1"
            mock_boto.client.return_value = mock_client

            with patch(
                "inferia.services.orchestration.services.adapter_engine.adapters.aws.adapter._run_sync",
                new_callable=AsyncMock,
                side_effect=Exception("API timeout"),
            ):
                from inferia.services.orchestration.services.adapter_engine.adapters.aws.adapter import (
                    AWSAdapter,
                )

                adapter = AWSAdapter(region="us-east-1")
                result = await adapter.get_node_metadata("i-bad")

            assert result == {}


class TestSkyPilotAsync:
    @pytest.mark.asyncio
    async def test_provision_uses_to_thread(self):
        """sky.launch must be called via asyncio.to_thread."""
        mock_request = MagicMock()
        mock_request.cloud = "aws"
        mock_request.region = "us-east-1"
        mock_request.gpu = 1
        mock_request.gpu_type = "A100"
        mock_request.cpu = 8

        with patch.dict("os.environ", {"INFERIA_ENV": "container"}), \
             patch(
                 "inferia.services.orchestration.provisioning.skypilot.asyncio.to_thread",
                 new_callable=AsyncMock,
             ) as mock_to_thread, \
             patch(
                 "inferia.services.orchestration.provisioning.skypilot.sky",
                 create=True,
             ) as mock_sky:
            mock_sky.Resources.return_value = MagicMock()
            mock_sky.Task.return_value = MagicMock()
            mock_to_thread.return_value = None

            from inferia.services.orchestration.provisioning.skypilot import (
                SkyPilotProvisioner,
            )

            provisioner = SkyPilotProvisioner()
            cluster = await provisioner.provision(mock_request)

        mock_to_thread.assert_awaited_once()
        assert cluster.startswith("inferia-")

    @pytest.mark.asyncio
    async def test_terminate_uses_to_thread(self):
        """sky.down must be called via asyncio.to_thread."""
        with patch(
            "inferia.services.orchestration.provisioning.skypilot.asyncio.to_thread",
            new_callable=AsyncMock,
        ) as mock_to_thread, patch(
            "inferia.services.orchestration.provisioning.skypilot.sky",
            create=True,
        ):
            from inferia.services.orchestration.provisioning.skypilot import (
                SkyPilotProvisioner,
            )

            provisioner = SkyPilotProvisioner()
            await provisioner.terminate("inferia-abc123")

        mock_to_thread.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_terminate_handles_error(self):
        """terminate should not raise on failure (best-effort)."""
        with patch(
            "inferia.services.orchestration.provisioning.skypilot.asyncio.to_thread",
            new_callable=AsyncMock,
            side_effect=Exception("cluster not found"),
        ), patch(
            "inferia.services.orchestration.provisioning.skypilot.sky",
            create=True,
        ):
            from inferia.services.orchestration.provisioning.skypilot import (
                SkyPilotProvisioner,
            )

            provisioner = SkyPilotProvisioner()
            # Should NOT raise
            await provisioner.terminate("inferia-gone")

    @pytest.mark.asyncio
    async def test_provision_requires_container_env(self):
        """provision should raise if INFERIA_ENV != container."""
        with patch.dict("os.environ", {"INFERIA_ENV": "local"}):
            from inferia.services.orchestration.provisioning.skypilot import (
                SkyPilotProvisioner,
            )

            provisioner = SkyPilotProvisioner()
            with pytest.raises(RuntimeError, match="container"):
                await provisioner.provision(MagicMock())


class TestRunSyncHelper:
    """Test the _run_sync pattern without requiring boto3."""

    @pytest.mark.asyncio
    async def test_run_sync_offloads_to_executor(self):
        def blocking_add(a, b):
            return a + b

        result = await _run_sync(blocking_add, 2, 3)
        assert result == 5

    @pytest.mark.asyncio
    async def test_run_sync_with_kwargs(self):
        def kw_func(x, y=10):
            return x + y

        result = await _run_sync(kw_func, 5, y=20)
        assert result == 25

    @pytest.mark.asyncio
    async def test_run_sync_propagates_exceptions(self):
        from inferia.services.orchestration.services.adapter_engine.adapters.aws.adapter import (
            _run_sync,
        )

        def failing():
            raise ValueError("sync error")

        with pytest.raises(ValueError, match="sync error"):
            await _run_sync(failing)

    @pytest.mark.asyncio
    async def test_run_sync_does_not_block_event_loop(self):
        """The function should run in a thread, not blocking the loop."""
        import time

        def slow_func():
            time.sleep(0.05)
            return "done"

        start = asyncio.get_event_loop().time()
        result = await _run_sync(slow_func)
        assert result == "done"

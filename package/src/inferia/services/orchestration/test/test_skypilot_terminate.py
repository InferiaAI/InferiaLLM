"""Tests for SkyPilotProvisioner.terminate() error logging."""

import logging
import pytest
from unittest.mock import patch, MagicMock


@pytest.fixture()
def _container_env(monkeypatch):
    """Pretend we are inside a container so the module-level guard passes."""
    monkeypatch.setenv("INFERIA_ENV", "container")


@pytest.fixture()
def _fake_sky(monkeypatch):
    """Inject a fake ``sky`` module so the real package is not required."""
    fake = MagicMock()
    monkeypatch.setitem(__import__("sys").modules, "sky", fake)
    return fake


@pytest.mark.usefixtures("_container_env", "_fake_sky")
class TestTerminateLogging:
    """SkyPilotProvisioner.terminate() must log errors instead of swallowing them."""

    @pytest.mark.asyncio
    async def test_terminate_logs_error_on_failure(self, _fake_sky, caplog):
        _fake_sky.down.side_effect = RuntimeError("cloud API timeout")

        # Import after env is set so the module-level guard passes.
        from inferia.services.orchestration.provisioning.skypilot import (
            SkyPilotProvisioner,
        )

        provisioner = SkyPilotProvisioner()

        with caplog.at_level(logging.ERROR):
            await provisioner.terminate("test-cluster-001")

        assert any(
            "Failed to terminate SkyPilot cluster test-cluster-001" in r.message
            for r in caplog.records
        ), "Expected an ERROR log about the failed termination"

    @pytest.mark.asyncio
    async def test_terminate_does_not_reraise(self, _fake_sky):
        _fake_sky.down.side_effect = RuntimeError("cloud API timeout")

        from inferia.services.orchestration.provisioning.skypilot import (
            SkyPilotProvisioner,
        )

        provisioner = SkyPilotProvisioner()

        # Must not raise — termination is best-effort.
        await provisioner.terminate("test-cluster-002")

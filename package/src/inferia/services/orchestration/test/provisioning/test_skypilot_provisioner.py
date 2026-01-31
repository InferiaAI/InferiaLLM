import pytest
import sys
from unittest.mock import MagicMock

from app.provisioning.skypilot import SkyPilotProvisioner


class DummyRequest:
    cloud = "aws"
    region = "us-east-1"
    gpu_type = "A100"
    gpu = 1
    cpu = 8


@pytest.mark.asyncio
async def test_provision_success(mocker, caplog):
    # Inject fake sky module BEFORE provision() imports it
    mock_sky = MagicMock()
    mocker.patch.dict(sys.modules, {"sky": mock_sky})

    caplog.set_level("INFO")

    provisioner = SkyPilotProvisioner()
    cluster_id = await provisioner.provision(DummyRequest())

    assert cluster_id.startswith("inferia-")
    mock_sky.launch.assert_called_once()


@pytest.mark.asyncio
async def test_provision_failure(mocker):
    mock_sky = MagicMock()
    mock_sky.launch.side_effect = Exception("SkyPilot internal error")
    mocker.patch.dict(sys.modules, {"sky": mock_sky})

    provisioner = SkyPilotProvisioner()

    with pytest.raises(RuntimeError):
        await provisioner.provision(DummyRequest())


@pytest.mark.asyncio
async def test_terminate_best_effort(mocker):
    mock_sky = MagicMock()
    mock_sky.down.side_effect = Exception("already deleted")
    mocker.patch.dict(sys.modules, {"sky": mock_sky})

    provisioner = SkyPilotProvisioner()

    # Must not raise
    await provisioner.terminate("inferia-dead")

"""Tests for PulumiAWSAdapter — happy provision + edge cases."""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest

from inferia.services.orchestration.services.adapter_engine.adapters.pulumi.credentials import (
    MissingCredentialsError,
)
from inferia.services.orchestration.services.adapter_engine.adapters.pulumi.pulumi_aws_adapter import (
    PulumiAWSAdapter,
    ProvisionError,
)


def _aws_cfg_dict(**kw):
    base = {
        "access_key_id": "AKIAREALKEY1234XYZ8",
        "secret_access_key": "real-secret-not-masked-1234567",
        "region": "us-east-1",
        "subnet_id": "subnet-0123456789abcdef0",
        "security_group_ids": ["sg-0123456789abcdef0"],
        "ami_id": "ami-0123456789abcdef0",
        "root_volume_gb": 200,
    }
    base.update(kw)
    return base


@pytest.fixture
def fake_db():
    db = MagicMock()
    db.execute = AsyncMock(return_value="INSERT 0 1")
    db.fetchrow = AsyncMock(return_value=None)
    return db


@pytest.fixture
def aws_config():
    from inferia.services.api_gateway.config import (
        AWSConfig, CloudConfig, ProvidersConfig,
    )
    return ProvidersConfig(cloud=CloudConfig(aws=AWSConfig(**_aws_cfg_dict())))


@pytest.mark.asyncio
async def test_provision_node_kicks_off_async_task(fake_db, aws_config, tmp_path):
    """provision_node returns immediately with state=provisioning and
    schedules a background task that calls stack.up_async."""
    pool_id = "00000000-0000-0000-0000-000000000001"
    org_id = "11111111-1111-1111-1111-111111111111"

    fake_stack = MagicMock()
    fake_stack.up_async = AsyncMock(return_value=MagicMock(outputs={}))
    fake_stack.set_config = MagicMock()

    with patch(
        "inferia.services.orchestration.services.adapter_engine.adapters.pulumi.pulumi_aws_adapter."
        "pulumi.automation.create_or_select_stack",
        return_value=fake_stack,
    ), patch(
        "inferia.services.orchestration.services.adapter_engine.adapters.pulumi.pulumi_aws_adapter."
        "load_providers_config",
        return_value=aws_config,
    ), patch(
        "inferia.services.orchestration.services.adapter_engine.adapters.pulumi.pulumi_aws_adapter."
        "mint_bootstrap_token",
        new=AsyncMock(return_value=("tok-xyz", UUID(int=42))),
    ):
        adapter = PulumiAWSAdapter(db=fake_db, state_dir=str(tmp_path))
        result = await adapter.provision_node(
            provider_resource_id="g5.xlarge",
            pool_id=pool_id,
            org_id=org_id,
            region="us-east-1",
        )

    assert result["provider"] == "aws"
    assert result["lifecycle_state"] == "provisioning"
    assert result["region"] == "us-east-1"
    assert result["metadata"]["pulumi_stack"] == f"inferia-pool-{pool_id}"

    # The background task must have been created. Cancel/await so the
    # test doesn't leak.
    for t in list(asyncio.all_tasks()):
        if t.get_coro() and t.get_coro().__name__ == "_provision_async":
            t.cancel()
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass


@pytest.mark.asyncio
async def test_provision_node_missing_creds_raises(fake_db, tmp_path):
    from inferia.services.api_gateway.config import (
        AWSConfig, CloudConfig, ProvidersConfig,
    )
    empty_cfg = ProvidersConfig(cloud=CloudConfig(aws=AWSConfig()))
    with patch(
        "inferia.services.orchestration.services.adapter_engine.adapters.pulumi.pulumi_aws_adapter."
        "load_providers_config",
        return_value=empty_cfg,
    ):
        adapter = PulumiAWSAdapter(db=fake_db, state_dir=str(tmp_path))
        with pytest.raises(MissingCredentialsError):
            await adapter.provision_node(
                provider_resource_id="g5.xlarge",
                pool_id="x",
                org_id="x",
                region="us-east-1",
            )
    # Crucially: NO bootstrap token DB write happened.
    fake_db.execute.assert_not_called()


@pytest.mark.asyncio
async def test_provision_node_invalid_metadata_rejected(fake_db, aws_config, tmp_path):
    """A pool with metadata={subnet_id: 'bogus'} must be rejected by
    AWSPoolMetadata before any AWS call."""
    with patch(
        "inferia.services.orchestration.services.adapter_engine.adapters.pulumi.pulumi_aws_adapter."
        "load_providers_config",
        return_value=aws_config,
    ):
        adapter = PulumiAWSAdapter(db=fake_db, state_dir=str(tmp_path))
        with pytest.raises(ProvisionError):
            await adapter.provision_node(
                provider_resource_id="g5.xlarge",
                pool_id="x",
                org_id="x",
                region="us-east-1",
                metadata={"subnet_id": "bogus", "security_group_ids": ["sg-abcdef012"]},
            )

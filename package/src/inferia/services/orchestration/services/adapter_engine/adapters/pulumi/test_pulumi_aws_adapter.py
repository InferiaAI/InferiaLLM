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


@pytest.mark.asyncio
async def test_provision_async_failure_marks_pool_failed(fake_db, aws_config, tmp_path):
    """When stack.up_async raises, the pool moves to lifecycle_state='failed'
    and the error message lands in metadata.error."""
    fake_stack = MagicMock()
    fake_stack.up_async = AsyncMock(side_effect=Exception("InsufficientInstanceCapacity"))
    fake_stack.destroy_async = AsyncMock(return_value=None)
    adapter = PulumiAWSAdapter(db=fake_db, state_dir=str(tmp_path))
    await adapter._provision_async(fake_stack, "00000000-0000-0000-0000-000000000001", "boot-1")
    # _db.execute called with UPDATE compute_pools ... lifecycle_state='failed'
    failed_call = any(
        "lifecycle_state" in str(c) and "'failed'" in str(c)
        for c in fake_db.execute.call_args_list
    )
    assert failed_call, fake_db.execute.call_args_list
    fake_stack.destroy_async.assert_awaited_once()


@pytest.mark.asyncio
async def test_provision_async_success_writes_outputs(fake_db, aws_config, tmp_path):
    """On stack.up_async success, instance_id/public_dns/private_ip go into
    compute_pools.metadata via a jsonb-merge UPDATE."""
    fake_stack = MagicMock()
    fake_output = lambda v: MagicMock(value=v)
    fake_stack.up_async = AsyncMock(return_value=MagicMock(outputs={
        "instance_id": fake_output("i-abc"),
        "public_dns":  fake_output("ec2-1-2-3.amazon.com"),
        "private_ip":  fake_output("10.0.0.5"),
    }))
    adapter = PulumiAWSAdapter(db=fake_db, state_dir=str(tmp_path))
    await adapter._provision_async(fake_stack, "00000000-0000-0000-0000-000000000001", "boot-1")
    write_call = next(
        c for c in fake_db.execute.call_args_list
        if "compute_pools" in str(c) and "metadata" in str(c)
    )
    body = str(write_call)
    assert "i-abc" in body and "10.0.0.5" in body


@pytest.mark.asyncio
async def test_wait_for_ready_polls_until_inventory_ready(fake_db, aws_config, tmp_path):
    fake_db.fetchrow = AsyncMock(side_effect=[
        {"state": "pending"},
        {"state": "pending"},
        {"state": "ready"},
    ])
    adapter = PulumiAWSAdapter(db=fake_db, state_dir=str(tmp_path))
    result = await adapter.wait_for_ready(
        provider_instance_id="boot-1",
        timeout=30,
        poll_interval=0.01,
    )
    assert result == "ready"
    assert fake_db.fetchrow.call_count == 3


@pytest.mark.asyncio
async def test_wait_for_ready_timeout_destroys_stack(fake_db, aws_config, tmp_path):
    fake_db.fetchrow = AsyncMock(return_value={"state": "pending"})
    fake_stack = MagicMock()
    fake_stack.destroy_async = AsyncMock(return_value=None)
    adapter = PulumiAWSAdapter(db=fake_db, state_dir=str(tmp_path))
    with patch.object(adapter, "_select_stack", return_value=fake_stack), patch(
        "inferia.services.orchestration.services.adapter_engine.adapters.pulumi.pulumi_aws_adapter."
        "load_providers_config",
        return_value=aws_config,
    ):
        with pytest.raises(ProvisionError):
            await adapter.wait_for_ready(
                provider_instance_id="boot-1",
                timeout=0.05,
                poll_interval=0.01,
            )
    fake_stack.destroy_async.assert_awaited_once()


@pytest.mark.asyncio
async def test_deprovision_node_destroys_stack(fake_db, aws_config, tmp_path):
    fake_stack = MagicMock()
    fake_stack.destroy_async = AsyncMock(return_value=None)
    fake_stack.workspace = MagicMock()
    fake_stack.workspace.remove_stack = MagicMock()
    adapter = PulumiAWSAdapter(db=fake_db, state_dir=str(tmp_path))
    with patch.object(adapter, "_select_stack", return_value=fake_stack), patch(
        "inferia.services.orchestration.services.adapter_engine.adapters.pulumi.pulumi_aws_adapter."
        "load_providers_config",
        return_value=aws_config,
    ):
        await adapter.deprovision_node(provider_instance_id="00000000-0000-0000-0000-000000000001")
    fake_stack.destroy_async.assert_awaited_once()


@pytest.mark.asyncio
async def test_discover_resources_normalizes_output(fake_db, aws_config, tmp_path):
    mock_ec2 = MagicMock()
    mock_ec2.describe_instance_types.return_value = {
        "InstanceTypes": [
            {
                "InstanceType": "g5.xlarge",
                "VCpuInfo": {"DefaultVCpus": 4},
                "MemoryInfo": {"SizeInMiB": 16384},
                "GpuInfo": {"Gpus": [{"Name": "A10G", "Count": 1,
                                       "Manufacturer": "NVIDIA",
                                       "MemoryInfo": {"SizeInMiB": 24576}}]},
            },
            {
                "InstanceType": "m5.large",
                "VCpuInfo": {"DefaultVCpus": 2},
                "MemoryInfo": {"SizeInMiB": 8192},
            },
        ]
    }
    with patch("boto3.client", return_value=mock_ec2), patch(
        "inferia.services.orchestration.services.adapter_engine.adapters.pulumi.pulumi_aws_adapter."
        "load_providers_config",
        return_value=aws_config,
    ):
        adapter = PulumiAWSAdapter(db=fake_db, state_dir=str(tmp_path))
        out = await adapter.discover_resources(region="us-east-1")
    by_type = {r["provider_resource_id"]: r for r in out}
    assert by_type["g5.xlarge"]["gpu_vendor"] == "nvidia"
    assert by_type["g5.xlarge"]["gpu_memory_gb"] == 24
    assert by_type["m5.large"]["gpu_vendor"] == "none"
    assert by_type["m5.large"]["gpu_count"] == 0


@pytest.mark.asyncio
async def test_get_logs_returns_console_output(fake_db, aws_config, tmp_path):
    mock_ec2 = MagicMock()
    mock_ec2.get_console_output.return_value = {"Output": "line1\nline2\n"}
    with patch("boto3.client", return_value=mock_ec2), patch(
        "inferia.services.orchestration.services.adapter_engine.adapters.pulumi.pulumi_aws_adapter."
        "load_providers_config",
        return_value=aws_config,
    ):
        adapter = PulumiAWSAdapter(db=fake_db, state_dir=str(tmp_path))
        logs = await adapter.get_logs(provider_instance_id="i-abc")
    assert logs == {"logs": ["line1", "line2"]}


@pytest.mark.asyncio
async def test_get_logs_error_returns_empty(fake_db, aws_config, tmp_path):
    mock_ec2 = MagicMock()
    mock_ec2.get_console_output.side_effect = Exception("AccessDenied")
    with patch("boto3.client", return_value=mock_ec2), patch(
        "inferia.services.orchestration.services.adapter_engine.adapters.pulumi.pulumi_aws_adapter."
        "load_providers_config",
        return_value=aws_config,
    ):
        adapter = PulumiAWSAdapter(db=fake_db, state_dir=str(tmp_path))
        logs = await adapter.get_logs(provider_instance_id="i-abc")
    assert logs == {"logs": []}

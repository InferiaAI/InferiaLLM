import time
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from inferia.services.orchestration.services.adapter_engine.adapters.aws import aws_discovery as d


def _fake_ec2(**calls):
    ec2 = MagicMock()
    for name, ret in calls.items():
        getattr(ec2, name).return_value = ret
    return ec2


@pytest.fixture(autouse=True)
def _clear_cache():
    d._CACHE.clear()
    yield
    d._CACHE.clear()


@pytest.mark.asyncio
async def test_list_regions_returns_enabled_sorted():
    ec2 = _fake_ec2(describe_regions={"Regions": [
        {"RegionName": "us-west-2"}, {"RegionName": "us-east-1"}]})
    with patch.object(d, "_ec2", return_value=ec2), \
         patch.object(d, "_resolve_creds", new=AsyncMock(return_value={"aws_access_key_id": "k", "aws_secret_access_key": "s"})):
        out = await d.list_regions()
    assert out == ["us-east-1", "us-west-2"]
    ec2.describe_regions.assert_called_once()


@pytest.mark.asyncio
async def test_list_regions_cached_second_call_no_aws():
    from unittest.mock import AsyncMock
    ec2 = _fake_ec2(describe_regions={"Regions": [{"RegionName": "us-east-1"}]})
    creds_mock = AsyncMock(return_value={"aws_access_key_id": "k", "aws_secret_access_key": "s"})
    with patch.object(d, "_ec2", return_value=ec2) as mk, \
         patch.object(d, "_resolve_creds", creds_mock):
        await d.list_regions()
        await d.list_regions()
    assert mk.call_count == 1
    assert creds_mock.call_count == 1


@pytest.mark.asyncio
async def test_list_regions_no_creds_raises_unavailable():
    with patch.object(d, "_resolve_creds", new=AsyncMock(return_value=None)):
        with pytest.raises(d.AwsDiscoveryUnavailable):
            await d.list_regions()


@pytest.mark.asyncio
async def test_list_regions_access_denied_raises_unavailable():
    from botocore.exceptions import ClientError
    ec2 = MagicMock()
    ec2.describe_regions.side_effect = ClientError({"Error": {"Code": "UnauthorizedOperation"}}, "DescribeRegions")
    with patch.object(d, "_ec2", return_value=ec2), \
         patch.object(d, "_resolve_creds", new=AsyncMock(return_value={"aws_access_key_id": "k", "aws_secret_access_key": "s"})):
        with pytest.raises(d.AwsDiscoveryUnavailable):
            await d.list_regions()


@pytest.mark.asyncio
async def test_list_regions_empty_raises_unavailable():
    ec2 = _fake_ec2(describe_regions={"Regions": []})
    with patch.object(d, "_ec2", return_value=ec2), \
         patch.object(d, "_resolve_creds", new=AsyncMock(return_value={"aws_access_key_id": "k", "aws_secret_access_key": "s"})):
        with pytest.raises(d.AwsDiscoveryUnavailable, match="no enabled regions"):
            await d.list_regions()


@pytest.mark.asyncio
async def test_list_instance_types_enriches_and_flags_gpu():
    from unittest.mock import AsyncMock
    ec2 = MagicMock()
    paginator = MagicMock()
    paginator.paginate.return_value = [
        {"InstanceTypeOfferings": [{"InstanceType": "g6.xlarge"}, {"InstanceType": "m5.large"}]},
    ]
    ec2.get_paginator.return_value = paginator
    ec2.describe_instance_types.return_value = {"InstanceTypes": [
        {"InstanceType": "g6.xlarge", "VCpuInfo": {"DefaultVCpus": 4},
         "MemoryInfo": {"SizeInMiB": 16384},
         "GpuInfo": {"Gpus": [{"Name": "L4", "Count": 1}]}},
        {"InstanceType": "m5.large", "VCpuInfo": {"DefaultVCpus": 2},
         "MemoryInfo": {"SizeInMiB": 8192}},
    ]}
    with patch.object(d, "_ec2", return_value=ec2), \
         patch.object(d, "_resolve_creds", AsyncMock(return_value={"aws_access_key_id": "k", "aws_secret_access_key": "s"})):
        out = await d.list_instance_types("us-east-1")
    by = {i["instance_type"]: i for i in (x.to_dict() for x in out)}
    assert by["g6.xlarge"]["is_gpu"] is True
    assert by["g6.xlarge"]["gpu_count"] == 1 and by["g6.xlarge"]["gpu_model"] == "L4"
    assert by["g6.xlarge"]["memory_gb"] == 16.0
    assert by["m5.large"]["is_gpu"] is False and by["m5.large"]["gpu_count"] == 0
    assert out[0].instance_type == "g6.xlarge"  # GPU sorts first


@pytest.mark.asyncio
async def test_list_instance_types_no_creds_raises():
    from unittest.mock import AsyncMock
    with patch.object(d, "_resolve_creds", AsyncMock(return_value=None)):
        with pytest.raises(d.AwsDiscoveryUnavailable):
            await d.list_instance_types("us-east-1")


@pytest.mark.asyncio
async def test_list_instance_types_batches_over_100():
    from unittest.mock import AsyncMock
    ec2 = MagicMock()
    names = [f"t{i}.x" for i in range(150)]
    paginator = MagicMock()
    paginator.paginate.return_value = [{"InstanceTypeOfferings": [{"InstanceType": n} for n in names]}]
    ec2.get_paginator.return_value = paginator
    ec2.describe_instance_types.return_value = {"InstanceTypes": []}
    with patch.object(d, "_ec2", return_value=ec2), \
         patch.object(d, "_resolve_creds", AsyncMock(return_value={"aws_access_key_id": "k", "aws_secret_access_key": "s"})):
        await d.list_instance_types("us-east-1")
    # 150 names → 2 describe_instance_types calls (100 + 50)
    assert ec2.describe_instance_types.call_count == 2

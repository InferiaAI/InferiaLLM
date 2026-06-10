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

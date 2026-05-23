"""Tests for latest_dlami_ami SSM lookup."""
import time
from unittest.mock import MagicMock, patch

import botocore.exceptions
import pytest

from inferia.services.orchestration.services.adapter_engine.adapters.pulumi import ami


def _fresh_cache():
    ami._DLAMI_CACHE.clear()


def test_latest_dlami_ami_returns_value():
    _fresh_cache()
    mock_ssm = MagicMock()
    mock_ssm.get_parameter.return_value = {"Parameter": {"Value": "ami-deadbeef"}}
    with patch("boto3.client", return_value=mock_ssm):
        out = ami.latest_dlami_ami("us-east-1")
    assert out == "ami-deadbeef"


def test_latest_dlami_ami_is_cached_per_region():
    _fresh_cache()
    mock_ssm = MagicMock()
    mock_ssm.get_parameter.return_value = {"Parameter": {"Value": "ami-abc"}}
    with patch("boto3.client", return_value=mock_ssm):
        ami.latest_dlami_ami("us-east-1")
        ami.latest_dlami_ami("us-east-1")
    assert mock_ssm.get_parameter.call_count == 1


def test_latest_dlami_ami_different_regions_independent():
    _fresh_cache()
    mock_ssm = MagicMock()
    mock_ssm.get_parameter.side_effect = [
        {"Parameter": {"Value": "ami-east"}},
        {"Parameter": {"Value": "ami-west"}},
    ]
    with patch("boto3.client", return_value=mock_ssm):
        e = ami.latest_dlami_ami("us-east-1")
        w = ami.latest_dlami_ami("us-west-2")
    assert e == "ami-east"
    assert w == "ami-west"
    assert mock_ssm.get_parameter.call_count == 2


def test_latest_dlami_ami_cache_expires():
    _fresh_cache()
    mock_ssm = MagicMock()
    mock_ssm.get_parameter.return_value = {"Parameter": {"Value": "ami-1"}}
    with patch("boto3.client", return_value=mock_ssm):
        ami.latest_dlami_ami("us-east-1")
        # Manually expire the cache.
        ami._DLAMI_CACHE["us-east-1"] = ("ami-1", time.time() - ami._DLAMI_TTL_S - 1)
        ami.latest_dlami_ami("us-east-1")
    assert mock_ssm.get_parameter.call_count == 2


def test_latest_dlami_ami_boto_error_raises():
    _fresh_cache()
    mock_ssm = MagicMock()
    mock_ssm.get_parameter.side_effect = botocore.exceptions.ClientError(
        {"Error": {"Code": "ParameterNotFound", "Message": "x"}}, "GetParameter",
    )
    with patch("boto3.client", return_value=mock_ssm):
        with pytest.raises(ami.AMILookupError):
            ami.latest_dlami_ami("us-east-1")

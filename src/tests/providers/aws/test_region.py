"""Unit tests for AWS region validation.

Regression: a pool created with a malformed region (e.g. ``us-east1`` instead
of ``us-east-1``) was accepted at /createpool and only failed much later at
preflight with a cryptic ``DLAMI lookup failed: EndpointConnectionError`` (the
SSM endpoint ``ssm.us-east1.amazonaws.com`` does not resolve). We validate the
region at the boundary and raise a clear, actionable error instead.
"""
from __future__ import annotations

import pytest

from providers.aws.region import (
    InvalidRegionError,
    is_valid_aws_region,
    validate_aws_region,
)


@pytest.mark.parametrize(
    "region",
    ["us-east-1", "us-west-2", "eu-west-2", "ap-southeast-1", "us-gov-west-1"],
)
def test_valid_regions_accepted(region):
    # Should not raise.
    validate_aws_region(region)
    assert is_valid_aws_region(region) is True


def test_missing_hyphen_typo_rejected():
    # The exact bug: us-east1 (no second hyphen) → unreachable SSM endpoint.
    assert is_valid_aws_region("us-east1") is False
    with pytest.raises(InvalidRegionError) as exc:
        validate_aws_region("us-east1")
    msg = str(exc.value)
    assert "us-east1" in msg
    # Message must be actionable: name the field and a correct example.
    assert "region" in msg.lower()
    assert "us-east-1" in msg


def test_empty_region_rejected():
    assert is_valid_aws_region("") is False
    with pytest.raises(InvalidRegionError):
        validate_aws_region("")


def test_none_region_rejected():
    assert is_valid_aws_region(None) is False
    with pytest.raises(InvalidRegionError):
        validate_aws_region(None)


def test_uppercase_region_rejected():
    # AWS region codes are lowercase; reject US-EAST-1 rather than silently
    # passing a value boto3 will fail to build an endpoint for.
    assert is_valid_aws_region("US-EAST-1") is False
    with pytest.raises(InvalidRegionError):
        validate_aws_region("US-EAST-1")


def test_unknown_but_well_formed_region_rejected():
    # Well-formed shape but not a real region — membership check must catch it.
    assert is_valid_aws_region("xx-nowhere-9") is False
    with pytest.raises(InvalidRegionError):
        validate_aws_region("xx-nowhere-9")


def test_whitespace_is_stripped_then_validated():
    # A trailing newline from a copy-paste should not by itself invalidate an
    # otherwise-correct region.
    validate_aws_region("us-east-1\n")
    assert is_valid_aws_region("  us-east-1  ") is True

"""Tests for AWSPoolMetadata Pydantic v2 model.

Covers: happy paths, each field rejection, boundary values, and
forward-compat extra-key behaviour.
"""
import pytest
from pydantic import ValidationError

from providers.aws.pool_metadata import (
    AWSPoolMetadata,
)


def test_minimal_valid():
    m = AWSPoolMetadata(
        subnet_id="subnet-0123456789abcdef0",
        security_group_ids=["sg-0123456789abcdef0"],
    )
    assert m.subnet_id == "subnet-0123456789abcdef0"
    assert m.security_group_ids == ["sg-0123456789abcdef0"]
    assert m.ami_id is None
    assert m.iam_instance_profile is None
    # All numeric fields default to None — the PulumiAWSAdapter falls back
    # to ProvidersConfig.cloud.aws account defaults when these are unset.
    assert m.root_volume_gb is None
    assert m.worker_image_tag is None


def test_empty_metadata_valid():
    """Empty dict is now valid — every field is an optional pool-level
    override of the account-wide defaults."""
    m = AWSPoolMetadata()
    assert m.subnet_id is None
    assert m.security_group_ids is None
    assert m.ami_id is None
    assert m.root_volume_gb is None


def test_full_valid():
    m = AWSPoolMetadata(
        subnet_id="subnet-abc12345",
        security_group_ids=["sg-abc12345", "sg-def67890"],
        ami_id="ami-deadbeef00000000",
        iam_instance_profile="arn:aws:iam::123456789012:instance-profile/worker-role",
        root_volume_gb=500,
        worker_image_tag="v1.2.3",
    )
    assert m.iam_instance_profile.endswith("/worker-role")
    assert m.root_volume_gb == 500


@pytest.mark.parametrize("subnet", ["", "subnet-", "subnet-XXX", "subnet-0123", "subnetabc12345", "vpc-abc12345"])
def test_invalid_subnet_id(subnet):
    with pytest.raises(ValidationError):
        AWSPoolMetadata(subnet_id=subnet, security_group_ids=["sg-abc12345"])


def test_empty_security_group_list_rejected():
    with pytest.raises(ValidationError):
        AWSPoolMetadata(subnet_id="subnet-abc12345", security_group_ids=[])


@pytest.mark.parametrize("sg", ["", "sg-", "sg-XXX", "subnet-abc12345"])
def test_invalid_security_group(sg):
    with pytest.raises(ValidationError):
        AWSPoolMetadata(subnet_id="subnet-abc12345", security_group_ids=[sg])


@pytest.mark.parametrize("ami", ["", "ami-", "ami-XXX", "subnet-abc12345"])
def test_invalid_ami(ami):
    with pytest.raises(ValidationError):
        AWSPoolMetadata(subnet_id="subnet-abc12345", security_group_ids=["sg-abc12345"], ami_id=ami)


@pytest.mark.parametrize("arn", ["", "arn:aws:iam::123:role/foo", "arn:aws:iam::xx:instance-profile/foo", "random text"])
def test_invalid_iam_profile(arn):
    with pytest.raises(ValidationError):
        AWSPoolMetadata(
            subnet_id="subnet-abc12345",
            security_group_ids=["sg-abc12345"],
            iam_instance_profile=arn,
        )


@pytest.mark.parametrize("gb", [0, 9, -1, 16385, 100000])
def test_invalid_root_volume(gb):
    with pytest.raises(ValidationError):
        AWSPoolMetadata(
            subnet_id="subnet-abc12345",
            security_group_ids=["sg-abc12345"],
            root_volume_gb=gb,
        )


def test_root_volume_boundaries():
    """10 and 16384 are the inclusive boundary."""
    AWSPoolMetadata(subnet_id="subnet-abc12345", security_group_ids=["sg-abc12345"], root_volume_gb=10)
    AWSPoolMetadata(subnet_id="subnet-abc12345", security_group_ids=["sg-abc12345"], root_volume_gb=16384)


@pytest.mark.parametrize("tag", ["", " ", "with space", "x" * 129])
def test_invalid_image_tag(tag):
    with pytest.raises(ValidationError):
        AWSPoolMetadata(
            subnet_id="subnet-abc12345",
            security_group_ids=["sg-abc12345"],
            worker_image_tag=tag,
        )


def test_extra_keys_preserved_but_ignored():
    """Forward-compat: unknown keys are silently ignored (extra='ignore')."""
    m = AWSPoolMetadata(
        subnet_id="subnet-abc12345",
        security_group_ids=["sg-abc12345"],
        future_feature="hello",  # type: ignore
    )
    dumped = m.model_dump(exclude_none=False)
    # extra='ignore' means extra keys are dropped — only known fields survive
    assert "subnet_id" in dumped
    # future_feature is dropped silently (not an error, not preserved)
    assert "future_feature" not in dumped

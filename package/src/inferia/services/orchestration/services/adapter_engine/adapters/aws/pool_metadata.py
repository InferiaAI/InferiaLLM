"""Pydantic v2 model for AWS pool metadata.

Validates the shape of compute_pools.metadata when provider="aws", so
bad data is rejected at the API boundary instead of failing at
AWSAdapter.provision_node.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field, field_validator


SUBNET_RE = r"^subnet-[0-9a-f]{8,17}$"
SG_RE = r"^sg-[0-9a-f]{8,17}$"
AMI_RE = r"^ami-[0-9a-f]{8,17}$"
IAM_PROFILE_RE = r"^arn:aws:iam::\d{12}:instance-profile/.+$"


class AWSPoolMetadata(BaseModel):
    """Schema for compute_pools.metadata when provider='aws'.

    All fields are optional pool-level overrides. When omitted, the
    PulumiAWSAdapter falls back to the account-wide defaults on
    ProvidersConfig.cloud.aws (configured in Settings → Providers → AWS).
    An empty `metadata: {}` is therefore valid and means "use account
    defaults entirely".
    """

    model_config = {"extra": "ignore"}  # forward-compat: ignore unknown keys

    subnet_id: Optional[str] = Field(default=None, pattern=SUBNET_RE)
    security_group_ids: Optional[list[str]] = Field(default=None, min_length=1)
    ami_id: Optional[str] = Field(default=None, pattern=AMI_RE)
    iam_instance_profile: Optional[str] = Field(default=None, pattern=IAM_PROFILE_RE)
    root_volume_gb: Optional[int] = Field(default=None, ge=10, le=16384)
    worker_image_tag: Optional[str] = Field(default=None, max_length=128)

    @field_validator("security_group_ids")
    @classmethod
    def _sg_format(cls, v: Optional[list[str]]) -> Optional[list[str]]:
        if v is None:
            return v
        import re
        compiled = re.compile(SG_RE)
        for sg in v:
            if not compiled.match(sg):
                raise ValueError(f"invalid security_group_id: {sg!r}")
        return v

    @field_validator("worker_image_tag")
    @classmethod
    def _tag_no_whitespace(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        if not v or any(c.isspace() for c in v):
            raise ValueError("worker_image_tag must be non-empty and contain no whitespace")
        return v

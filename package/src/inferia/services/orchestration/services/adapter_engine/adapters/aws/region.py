"""AWS region validation.

Validates a region code against the set of regions botocore knows about
(offline — from bundled endpoint data, no network call), across the standard,
GovCloud, and China partitions. This catches malformed codes like ``us-east1``
(missing the second hyphen) at the API boundary, instead of letting them reach
preflight where boto3 fails to build an endpoint and surfaces an opaque
``EndpointConnectionError``.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Optional

_PARTITIONS = ("aws", "aws-us-gov", "aws-cn")


class InvalidRegionError(ValueError):
    """Raised when a region code is not a known AWS region."""


@lru_cache(maxsize=1)
def _known_regions() -> frozenset[str]:
    """All EC2-capable region codes botocore knows, across partitions.

    Computed once and cached. Offline: uses botocore's bundled endpoint
    metadata, so it works inside a container with no AWS network access.
    """
    import boto3

    session = boto3.Session()
    regions: set[str] = set()
    for partition in _PARTITIONS:
        try:
            regions.update(session.get_available_regions("ec2", partition_name=partition))
        except Exception:
            # An unknown partition name in an older/newer botocore must not
            # break validation for the partitions that do resolve.
            continue
    return frozenset(regions)


def is_valid_aws_region(region: Optional[str]) -> bool:
    """Return True iff ``region`` is a known AWS region code.

    Tolerant of surrounding whitespace (copy-paste). Case-sensitive: AWS
    region codes are lowercase, so ``US-EAST-1`` is rejected.
    """
    if not isinstance(region, str):
        return False
    region = region.strip()
    if not region:
        return False
    return region in _known_regions()


def validate_aws_region(region: Optional[str]) -> None:
    """Raise :class:`InvalidRegionError` if ``region`` is not a valid AWS region.

    The message names the offending value and gives a correct example so the
    operator can fix the typo immediately.
    """
    if not is_valid_aws_region(region):
        raise InvalidRegionError(
            f"invalid AWS region {region!r}; expected a valid region code "
            f"such as 'us-east-1'"
        )

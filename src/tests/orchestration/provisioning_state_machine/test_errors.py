"""Tests for the ProvisioningError hierarchy."""
import pytest

from services.orchestration.provisioning_state_machine.errors import (
    ProvisioningError,
    TransientError, AWSThrottledError, AWSServerError,
    PulumiTransientError, NetworkError,
    PermanentError, PulumiCliMissingError, InvalidCredentialsError,
    InvalidSpecError, InvalidInstanceTypeError, AMINotFoundError,
    SubnetNotFoundError, SecurityGroupNotFoundError,
    InfrastructureError, QuotaExceededError, CapacityUnavailableError,
    SubnetExhaustedError,
)


# All non-base classes have a class-level `code` constant.
_ALL_TYPED = [
    (AWSThrottledError, "AWS_THROTTLED", TransientError),
    (AWSServerError, "AWS_5XX", TransientError),
    (PulumiTransientError, "PULUMI_TRANSIENT", TransientError),
    (NetworkError, "NETWORK_ERROR", TransientError),
    # Capacity self-heals → TransientError (auto-retried), not INFRASTRUCTURE.
    (CapacityUnavailableError, "INSUFFICIENT_CAPACITY", TransientError),
    (PulumiCliMissingError, "PULUMI_CLI_MISSING", PermanentError),
    (InvalidCredentialsError, "INVALID_CREDENTIALS", PermanentError),
    (InvalidSpecError, "INVALID_SPEC", PermanentError),
    (InvalidInstanceTypeError, "INVALID_INSTANCE_TYPE", PermanentError),
    (AMINotFoundError, "AMI_NOT_FOUND", PermanentError),
    (SubnetNotFoundError, "SUBNET_NOT_FOUND", PermanentError),
    (SecurityGroupNotFoundError, "SG_NOT_FOUND", PermanentError),
    (QuotaExceededError, "QUOTA_EXCEEDED", InfrastructureError),
    (SubnetExhaustedError, "SUBNET_EXHAUSTED", InfrastructureError),
]


@pytest.mark.parametrize("exc_cls, expected_code, expected_base", _ALL_TYPED)
def test_typed_error_has_class_level_code_and_base(exc_cls, expected_code, expected_base):
    """Each typed error has a class-level `code` and the right base class."""
    e = exc_cls("test message")
    assert e.code == expected_code
    assert isinstance(e, expected_base)
    assert isinstance(e, ProvisioningError)


def test_message_preserved_via_str():
    e = AWSThrottledError("hit AWS rate limit")
    assert str(e) == "hit AWS rate limit"


def test_hint_optional_and_overrideable():
    e = AMINotFoundError("ami-abc not in us-west-2", hint="try us-east-1")
    assert e.hint == "try us-east-1"


def test_hint_default_none():
    e = AWSServerError("EC2 returned 503")
    assert e.hint is None


def test_code_can_be_overridden_at_construction():
    """Some classifiers may want to set a more specific code at runtime."""
    e = TransientError("custom", code="CUSTOM_CODE")
    assert e.code == "CUSTOM_CODE"


def test_base_classes_form_a_hierarchy():
    """All three error classes inherit from ProvisioningError but are
    siblings of each other (no cross-class isinstance)."""
    t = TransientError("t")
    p = PermanentError("p")
    i = InfrastructureError("i")
    assert isinstance(t, ProvisioningError)
    assert isinstance(p, ProvisioningError)
    assert isinstance(i, ProvisioningError)
    assert not isinstance(t, PermanentError)
    assert not isinstance(p, InfrastructureError)
    assert not isinstance(i, TransientError)

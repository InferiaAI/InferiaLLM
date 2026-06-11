"""Tests for the classify_error function."""
from __future__ import annotations

import asyncio
import socket

import pytest

from orchestration.state_machine.errors import (
    AMINotFoundError, AWSServerError, AWSThrottledError,
    CapacityUnavailableError, InvalidCredentialsError, InvalidInstanceTypeError,
    NetworkError, PulumiCliMissingError, PulumiTransientError,
    QuotaExceededError, SecurityGroupNotFoundError, SubnetNotFoundError,
)
from orchestration.state_machine.jobs.model import ErrorClass
from orchestration.state_machine.retry.classifier import (
    classify_error,
)


# ---- typed exception passthrough -----------------------------------------


@pytest.mark.parametrize("exc, expected_code, expected_class", [
    (AWSThrottledError("x"),             "AWS_THROTTLED",         ErrorClass.TRANSIENT),
    (AWSServerError("x"),                "AWS_5XX",               ErrorClass.TRANSIENT),
    (PulumiTransientError("x"),          "PULUMI_TRANSIENT",      ErrorClass.TRANSIENT),
    (NetworkError("x"),                  "NETWORK_ERROR",         ErrorClass.TRANSIENT),
    (PulumiCliMissingError("x"),         "PULUMI_CLI_MISSING",    ErrorClass.PERMANENT),
    (InvalidCredentialsError("x"),       "INVALID_CREDENTIALS",   ErrorClass.PERMANENT),
    (AMINotFoundError("x"),              "AMI_NOT_FOUND",         ErrorClass.PERMANENT),
    (SubnetNotFoundError("x"),           "SUBNET_NOT_FOUND",      ErrorClass.PERMANENT),
    (SecurityGroupNotFoundError("x"),    "SG_NOT_FOUND",          ErrorClass.PERMANENT),
    (InvalidInstanceTypeError("x"),      "INVALID_INSTANCE_TYPE", ErrorClass.PERMANENT),
    (QuotaExceededError("x"),            "QUOTA_EXCEEDED",        ErrorClass.INFRASTRUCTURE),
    # Capacity self-heals → TRANSIENT (auto-retried), unlike quota/subnet.
    (CapacityUnavailableError("x"),      "INSUFFICIENT_CAPACITY", ErrorClass.TRANSIENT),
])
def test_typed_provisioning_errors_passthrough(exc, expected_code, expected_class):
    ce = classify_error(exc)
    assert ce.code == expected_code
    assert ce.error_class == expected_class


def test_hint_preserved_from_typed_error():
    exc = AMINotFoundError("ami-x not in us-west-2", hint="try us-east-1")
    ce = classify_error(exc)
    assert ce.hint == "try us-east-1"


# ---- botocore.ClientError mapping ---------------------------------------


def _client_error(code: str, msg: str = "boom"):
    """Build a fake botocore.ClientError without importing botocore."""
    from botocore.exceptions import ClientError
    return ClientError(
        error_response={"Error": {"Code": code, "Message": msg}},
        operation_name="RunInstances",
    )


@pytest.mark.parametrize("aws_code, expected_code, expected_class", [
    ("RequestLimitExceeded",        "AWS_THROTTLED",         ErrorClass.TRANSIENT),
    ("Throttling",                  "AWS_THROTTLED",         ErrorClass.TRANSIENT),
    ("ThrottlingException",         "AWS_THROTTLED",         ErrorClass.TRANSIENT),
    ("AuthFailure",                 "INVALID_CREDENTIALS",   ErrorClass.PERMANENT),
    ("UnauthorizedOperation",       "INVALID_CREDENTIALS",   ErrorClass.PERMANENT),
    ("InvalidClientTokenId",        "INVALID_CREDENTIALS",   ErrorClass.PERMANENT),
    ("SignatureDoesNotMatch",       "INVALID_CREDENTIALS",   ErrorClass.PERMANENT),
    ("InvalidAMIID.NotFound",       "AMI_NOT_FOUND",         ErrorClass.PERMANENT),
    ("InvalidSubnetID.NotFound",    "SUBNET_NOT_FOUND",      ErrorClass.PERMANENT),
    ("InvalidGroup.NotFound",       "SG_NOT_FOUND",          ErrorClass.PERMANENT),
    ("VcpuLimitExceeded",           "QUOTA_EXCEEDED",        ErrorClass.INFRASTRUCTURE),
    ("InstanceLimitExceeded",       "QUOTA_EXCEEDED",        ErrorClass.INFRASTRUCTURE),
    ("InsufficientInstanceCapacity",    "INSUFFICIENT_CAPACITY", ErrorClass.TRANSIENT),
    ("InsufficientSpotInstanceCapacity","INSUFFICIENT_CAPACITY", ErrorClass.TRANSIENT),
])
def test_botocore_error_codes_map_correctly(aws_code, expected_code, expected_class):
    exc = _client_error(aws_code)
    ce = classify_error(exc)
    assert ce.code == expected_code, f"AWS code {aws_code} → {ce.code} (expected {expected_code})"
    assert ce.error_class == expected_class


def test_botocore_5xx_unknown_code_maps_to_aws_5xx():
    """A ClientError with no specific Code but 5xx status → AWS_5XX."""
    from botocore.exceptions import ClientError
    exc = ClientError(
        error_response={
            "Error": {"Code": "InternalServerError", "Message": "boom"},
            "ResponseMetadata": {"HTTPStatusCode": 503},
        },
        operation_name="RunInstances",
    )
    ce = classify_error(exc)
    assert ce.error_class == ErrorClass.TRANSIENT
    assert ce.code == "AWS_5XX"


def test_botocore_invalid_parameter_value_maps_to_instance_type():
    """InvalidParameterValue typically means a malformed instance type."""
    exc = _client_error("InvalidParameterValue", "Invalid instance type: zz")
    ce = classify_error(exc)
    assert ce.code == "INVALID_INSTANCE_TYPE"


# ---- network errors -----------------------------------------------------


def test_socket_gaierror_maps_to_network_error():
    ce = classify_error(socket.gaierror(-2, "name resolution failed"))
    assert ce.code == "NETWORK_ERROR"
    assert ce.error_class == ErrorClass.TRANSIENT


def test_connection_refused_maps_to_network_error():
    ce = classify_error(ConnectionRefusedError("connection refused"))
    assert ce.code == "NETWORK_ERROR"
    assert ce.error_class == ErrorClass.TRANSIENT


def test_asyncio_timeout_maps_to_network_error():
    ce = classify_error(asyncio.TimeoutError("upstream timeout"))
    assert ce.code == "NETWORK_ERROR"
    assert ce.error_class == ErrorClass.TRANSIENT


# ---- unknown → UNCLASSIFIED PERMANENT (fail-loud) -----------------------


def test_unknown_exception_is_unclassified_permanent():
    class Mystery(Exception):
        pass
    ce = classify_error(Mystery("something weird"))
    assert ce.code == "UNCLASSIFIED"
    assert ce.error_class == ErrorClass.PERMANENT
    # Message should include the type repr so an operator can file a bug.
    assert "Mystery" in ce.message


# ---- propagation: never classify these as failures ----------------------


def test_cancelled_error_propagates():
    """asyncio.CancelledError must NOT be classified; it bubbles up so
    handlers and the reconciler can do orderly shutdown."""
    with pytest.raises(asyncio.CancelledError):
        classify_error(asyncio.CancelledError())


def test_keyboard_interrupt_propagates():
    with pytest.raises(KeyboardInterrupt):
        classify_error(KeyboardInterrupt())


# ---- hints ----------------------------------------------------------------


def test_invalid_credentials_hint_includes_settings_path():
    """Operator-facing hint must mention where to fix the creds."""
    exc = _client_error("AuthFailure")
    ce = classify_error(exc)
    assert ce.hint is not None
    assert "Settings" in ce.hint or "Providers" in ce.hint


def test_pulumi_cli_missing_hint_includes_install_command():
    exc = PulumiCliMissingError("no pulumi binary")
    ce = classify_error(exc)
    # The typed error may not carry a hint by default; classifier should add one.
    assert ce.hint is not None
    assert "pulumi.com" in ce.hint


def test_bare_provisioning_error_classified_as_permanent():
    """Direct ProvisioningError (not a subclass) → PERMANENT."""
    from orchestration.state_machine.errors import (
        ProvisioningError,
    )
    exc = ProvisioningError("raw error")
    ce = classify_error(exc)
    assert ce.error_class == ErrorClass.PERMANENT


def test_botocore_unknown_code_with_4xx_status_is_unclassified():
    """Unknown AWS code + 4xx → UNCLASSIFIED PERMANENT (not AWS_5XX)."""
    from botocore.exceptions import ClientError
    exc = ClientError(
        error_response={
            "Error": {"Code": "SomeNewCode4xx", "Message": "boom"},
            "ResponseMetadata": {"HTTPStatusCode": 404},
        },
        operation_name="RunInstances",
    )
    ce = classify_error(exc)
    assert ce.code == "UNCLASSIFIED"
    assert ce.error_class == ErrorClass.PERMANENT
    assert "SomeNewCode4xx" in ce.message


# ---- Pulumi-wrapped AWS errors (string, not botocore ClientError) ----------
#
# The EC2-launch path runs `pulumi up`, whose automation API raises a
# CommandError whose str() embeds the RunInstances error (e.g.
# "api error VcpuLimitExceeded: ..."). It is NOT a botocore ClientError, so
# without a string-scan fallback every such error became UNCLASSIFIED PERMANENT
# — no clear message and no capacity auto-retry. These lock in the scan.


def _pulumi_err(aws_code: str) -> Exception:
    """Mimic a pulumi.automation CommandError: a plain exception whose message
    embeds the AWS RunInstances error inside the Pulumi stdout."""
    return Exception(
        "CommandError: \n code: 1\n stdout: Updating (inferia-x):\n"
        f"    error: creating EC2 Instance: operation error EC2: RunInstances, "
        f"https response error StatusCode: 400, api error {aws_code}: "
        "You have requested more vCPU capacity than your current vCPU limit of 8 "
        "allows ...: provider=aws@6.83.4"
    )


@pytest.mark.parametrize("aws_code, expected_code, expected_class", [
    ("VcpuLimitExceeded",                "QUOTA_EXCEEDED",        ErrorClass.INFRASTRUCTURE),
    ("InstanceLimitExceeded",            "QUOTA_EXCEEDED",        ErrorClass.INFRASTRUCTURE),
    ("InsufficientInstanceCapacity",     "INSUFFICIENT_CAPACITY", ErrorClass.TRANSIENT),
    ("InsufficientSpotInstanceCapacity", "INSUFFICIENT_CAPACITY", ErrorClass.TRANSIENT),
    ("InvalidAMIID.NotFound",            "AMI_NOT_FOUND",         ErrorClass.PERMANENT),
    ("AuthFailure",                      "INVALID_CREDENTIALS",   ErrorClass.PERMANENT),
])
def test_pulumi_wrapped_aws_error_is_classified(aws_code, expected_code, expected_class):
    ce = classify_error(_pulumi_err(aws_code))
    assert ce.code == expected_code, f"{aws_code} → {ce.code}"
    assert ce.error_class == expected_class


def test_pulumi_quota_error_carries_actionable_hint():
    ce = classify_error(_pulumi_err("VcpuLimitExceeded"))
    assert ce.hint is not None
    assert "quota" in ce.hint.lower() or "limit" in ce.hint.lower()


def test_pulumi_error_without_known_aws_code_is_unclassified():
    """A Pulumi failure with no recognizable AWS code still fails loud."""
    ce = classify_error(Exception("CommandError: code 1\n stdout: state file is locked"))
    assert ce.code == "UNCLASSIFIED"
    assert ce.error_class == ErrorClass.PERMANENT


def test_classify_error_safe_against_broken_str():
    """If exc.__str__ raises, classify_error still returns a ClassifiedError
    instead of propagating."""
    class Pathological(Exception):
        def __str__(self):
            raise RuntimeError("__str__ broken")
    ce = classify_error(Pathological())
    assert ce.error_class == ErrorClass.PERMANENT
    assert ce.code == "UNCLASSIFIED"
    assert "Pathological" in ce.message

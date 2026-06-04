"""Single source of truth for error → retry-decision classification.

Every place that catches an exception from a phase handler MUST go
through classify_error. Adding a new known error = one entry below.

asyncio.CancelledError and KeyboardInterrupt are deliberately re-raised
so handlers and the reconciler can do orderly shutdown — they are NOT
classified as failures.
"""
from __future__ import annotations

import asyncio
import socket
from typing import Any

from inferia.services.orchestration.services.provisioning.errors import (
    AMINotFoundError, AWSServerError, AWSThrottledError,
    CapacityUnavailableError, InfrastructureError, InvalidCredentialsError,
    InvalidInstanceTypeError, NetworkError, PermanentError,
    PulumiCliMissingError, PulumiTransientError, ProvisioningError,
    QuotaExceededError, SecurityGroupNotFoundError,
    SubnetNotFoundError, TransientError,
)
from inferia.services.orchestration.services.provisioning.jobs.model import (
    ClassifiedError, ErrorClass,
)


def _safe_str(exc: BaseException) -> str:
    """`str(exc)` can raise if the exception's __str__ is broken.
    The classifier must never compound failures via its own exception."""
    try:
        return str(exc)
    except BaseException:  # noqa: BLE001 - defensive guard
        return f"<{type(exc).__name__}: __str__ raised>"


# Hints attached at classification time for the typed errors that don't
# carry one by default. Keep these aligned with the spec's
# "Error → UI rendering" table.
_DEFAULT_HINTS: dict[str, str] = {
    "PULUMI_CLI_MISSING":     "Install in the inferia-app container: "
                              "curl -fsSL https://get.pulumi.com | sh",
    "INVALID_SPEC":           "The provisioning spec is missing required "
                              "fields or has invalid values. Review the "
                              "pool configuration in the New Pool wizard.",
    "INVALID_CREDENTIALS":    "Open Settings → Providers → AWS and re-enter "
                              "your access key.",
    "AMI_NOT_FOUND":          "The AMI is not available in the chosen region. "
                              "Try us-east-1 or pick a different AMI.",
    "SUBNET_NOT_FOUND":       "The configured subnet does not exist in this region. "
                              "Update Settings → Providers → AWS.",
    "SG_NOT_FOUND":           "The configured security group does not exist. "
                              "Update Settings → Providers → AWS.",
    "INVALID_INSTANCE_TYPE":  "The selected instance type is unknown or "
                              "unavailable in the region.",
    "QUOTA_EXCEEDED":         "Request a quota increase from AWS Support for "
                              "the relevant instance family in the region.",
    "INSUFFICIENT_CAPACITY":  "AWS has no spare capacity for this instance "
                              "type/AZ. Retrying automatically with backoff; "
                              "if it persists, try a different AZ or instance "
                              "type. Spot is especially prone to this.",
    "SUBNET_EXHAUSTED":       "The subnet has no free IPs. Use a different "
                              "subnet or expand the CIDR.",
    "AWS_THROTTLED":          "AWS rate limited the request. The reconciler "
                              "will back off and retry automatically.",
    "AWS_5XX":                "AWS returned a server error. The reconciler "
                              "will back off and retry automatically.",
    "PULUMI_TRANSIENT":       "Pulumi reported a transient error. Retrying "
                              "automatically.",
    "NETWORK_ERROR":          "Network connectivity issue. Retrying automatically.",
    "UNCLASSIFIED":           "This was not a known error. The full stack "
                              "trace is in the Logs tab. Please file a bug.",
}


# Botocore error code → typed exception class.
_AWS_CODE_MAP: dict[str, type[ProvisioningError]] = {
    "RequestLimitExceeded":           AWSThrottledError,
    "Throttling":                     AWSThrottledError,
    "ThrottlingException":            AWSThrottledError,
    "AuthFailure":                    InvalidCredentialsError,
    "UnauthorizedOperation":          InvalidCredentialsError,
    "InvalidClientTokenId":           InvalidCredentialsError,
    "SignatureDoesNotMatch":          InvalidCredentialsError,
    "InvalidAMIID.NotFound":          AMINotFoundError,
    "InvalidSubnetID.NotFound":       SubnetNotFoundError,
    "InvalidGroup.NotFound":          SecurityGroupNotFoundError,
    "VcpuLimitExceeded":              QuotaExceededError,
    "InstanceLimitExceeded":          QuotaExceededError,
    "InsufficientInstanceCapacity":      CapacityUnavailableError,
    "InsufficientSpotInstanceCapacity":  CapacityUnavailableError,
    "InvalidParameterValue":             InvalidInstanceTypeError,
}


def classify_error(exc: BaseException) -> ClassifiedError:
    """Map any exception → ClassifiedError.

    Raises asyncio.CancelledError / KeyboardInterrupt through unchanged."""
    if isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt)):
        raise exc

    # 1. Typed ProvisioningError → use its declared code + class.
    if isinstance(exc, ProvisioningError):
        return _build(exc.code, _err_class(exc), _safe_str(exc), exc.hint)

    # 2. botocore.ClientError → map by AWS code, fall back to 5xx detection.
    try:
        from botocore.exceptions import ClientError  # type: ignore
        if isinstance(exc, ClientError):
            return _classify_aws_client_error(exc)
    except ImportError:
        pass  # botocore not present in some test environments

    # 3. Network-ish exceptions → NetworkError.
    if isinstance(exc, (socket.gaierror, ConnectionError,
                        ConnectionRefusedError, ConnectionResetError,
                        TimeoutError, asyncio.TimeoutError)):
        return _build("NETWORK_ERROR", ErrorClass.TRANSIENT, _safe_str(exc) or repr(exc))

    # 4. Fall back: UNCLASSIFIED PERMANENT (fail loud, include type for triage).
    return _build(
        "UNCLASSIFIED",
        ErrorClass.PERMANENT,
        f"{type(exc).__name__}: {_safe_str(exc)}",
    )


def _classify_aws_client_error(exc: Any) -> ClassifiedError:
    code = (exc.response.get("Error") or {}).get("Code", "")
    status = (exc.response.get("ResponseMetadata") or {}).get("HTTPStatusCode")
    cls = _AWS_CODE_MAP.get(code)
    if cls is not None:
        return _build(cls.code, _class_to_error_class(cls), _safe_str(exc))
    if isinstance(status, int) and 500 <= status < 600:
        return _build("AWS_5XX", ErrorClass.TRANSIENT, _safe_str(exc))
    # Unknown AWS error → UNCLASSIFIED PERMANENT.
    return _build(
        "UNCLASSIFIED",
        ErrorClass.PERMANENT,
        f"AWS ClientError code={code} status={status}: {_safe_str(exc)}",
    )


def _err_class(exc: ProvisioningError) -> ErrorClass:
    if isinstance(exc, TransientError):
        return ErrorClass.TRANSIENT
    if isinstance(exc, InfrastructureError):
        return ErrorClass.INFRASTRUCTURE
    if isinstance(exc, PermanentError):
        return ErrorClass.PERMANENT
    # Shouldn't happen — ProvisioningError directly raised. Treat as permanent.
    return ErrorClass.PERMANENT


def _class_to_error_class(cls: type[ProvisioningError]) -> ErrorClass:
    if issubclass(cls, TransientError):
        return ErrorClass.TRANSIENT
    if issubclass(cls, InfrastructureError):
        return ErrorClass.INFRASTRUCTURE
    return ErrorClass.PERMANENT


def _build(
    code: str,
    error_class: ErrorClass,
    message: str,
    hint: str | None = None,
) -> ClassifiedError:
    if hint is None:
        hint = _DEFAULT_HINTS.get(code)
    return ClassifiedError(error_class=error_class, code=code, message=message, hint=hint)

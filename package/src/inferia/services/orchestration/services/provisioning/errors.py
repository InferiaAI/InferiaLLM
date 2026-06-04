"""Typed exception hierarchy for the provisioning state machine.

Phase handlers raise these (or unknown exceptions). The retry/classifier
module is the single source of truth for retry-vs-fail decisions.

Convention: each concrete subclass declares a class-level `code` (kept
in sync with the docs/specs/2026-05-27-aws-ec2-node-allocation-design.md
error-rendering table). The runtime constructor allows overriding the
code for cases where the classifier wants to be more specific.
"""
from __future__ import annotations


class ProvisioningError(Exception):
    """Base class. Subclasses carry code + optional hint."""

    code: str = "UNCLASSIFIED"
    hint: str | None = None

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        hint: str | None = None,
    ):
        super().__init__(message)
        if code is not None:
            self.code = code
        if hint is not None:
            self.hint = hint


# --- TRANSIENT -------------------------------------------------------------


class TransientError(ProvisioningError):
    """Retryable. Reconciler schedules a backoff and re-runs the phase."""


class AWSThrottledError(TransientError):
    code = "AWS_THROTTLED"


class AWSServerError(TransientError):
    code = "AWS_5XX"


class PulumiTransientError(TransientError):
    code = "PULUMI_TRANSIENT"


class NetworkError(TransientError):
    code = "NETWORK_ERROR"


class CapacityUnavailableError(TransientError):
    """AWS has no spare capacity for the instance type/AZ right now.

    Unlike quota/subnet exhaustion (which need an operator action and never
    self-heal), capacity routinely returns within minutes — especially during
    launch storms — so it is RETRYABLE. After TRANSIENT_MAX_ATTEMPTS a
    sustained shortage escalates to RETRIES_EXHAUSTED with the capacity hint
    preserved, prompting the operator to try a different AZ/instance type."""
    code = "INSUFFICIENT_CAPACITY"


# --- PERMANENT -------------------------------------------------------------


class PermanentError(ProvisioningError):
    """Not retryable. Phase transitions directly to 'failed'."""


class PulumiCliMissingError(PermanentError):
    code = "PULUMI_CLI_MISSING"


class InvalidCredentialsError(PermanentError):
    code = "INVALID_CREDENTIALS"


class InvalidSpecError(PermanentError):
    code = "INVALID_SPEC"


class InvalidInstanceTypeError(PermanentError):
    code = "INVALID_INSTANCE_TYPE"


class AMINotFoundError(PermanentError):
    code = "AMI_NOT_FOUND"


class SubnetNotFoundError(PermanentError):
    code = "SUBNET_NOT_FOUND"


class SecurityGroupNotFoundError(PermanentError):
    code = "SG_NOT_FOUND"


# --- INFRASTRUCTURE --------------------------------------------------------


class InfrastructureError(ProvisioningError):
    """Operator must take an AWS-account-level action (quota, subnet CIDR).
    Treated as PERMANENT for retry purposes — operator clicks Retry once
    the underlying issue is addressed. (Capacity is NOT here: it self-heals
    and is a TransientError above.)"""


class QuotaExceededError(InfrastructureError):
    code = "QUOTA_EXCEEDED"


class SubnetExhaustedError(InfrastructureError):
    code = "SUBNET_EXHAUSTED"

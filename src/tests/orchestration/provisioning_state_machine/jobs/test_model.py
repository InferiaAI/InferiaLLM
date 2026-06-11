"""Tests for ProvisioningJob model + Phase/ErrorClass enums + related dataclasses."""
from __future__ import annotations

import uuid
from dataclasses import FrozenInstanceError
from datetime import datetime, timezone

import pytest

from orchestration.provisioning_state_machine.jobs.model import (
    ClassifiedError,
    ErrorClass,
    EventLine,
    Phase,
    PhaseResult,
    ProvisioningJob,
)


# ---- Phase enum ----------------------------------------------------------


def test_phase_has_eight_values():
    assert {p.value for p in Phase} == {
        "pending", "preflight", "provisioning", "bootstrapping",
        "ready", "failed", "cancelling", "terminated",
    }


@pytest.mark.parametrize("phase, expected_terminal", [
    (Phase.PENDING,       False),
    (Phase.PREFLIGHT,     False),
    (Phase.PROVISIONING,  False),
    (Phase.BOOTSTRAPPING, False),
    (Phase.CANCELLING,    False),
    (Phase.READY,         True),
    (Phase.FAILED,        True),
    (Phase.TERMINATED,    True),
])
def test_phase_is_terminal(phase, expected_terminal):
    assert phase.is_terminal is expected_terminal


def test_phase_str_value_matches():
    assert Phase.READY == "ready"
    assert Phase.READY.value == "ready"


# ---- ErrorClass enum -----------------------------------------------------


def test_error_class_three_values():
    assert {e.value for e in ErrorClass} == {"TRANSIENT", "PERMANENT", "INFRASTRUCTURE"}


# ---- ClassifiedError dataclass -------------------------------------------


def test_classified_error_fields():
    ce = ClassifiedError(
        error_class=ErrorClass.PERMANENT,
        code="PULUMI_CLI_MISSING",
        message="pulumi binary not found",
        hint="curl pulumi.com | sh",
    )
    assert ce.error_class == ErrorClass.PERMANENT
    assert ce.code == "PULUMI_CLI_MISSING"
    assert ce.message == "pulumi binary not found"
    assert ce.hint == "curl pulumi.com | sh"


def test_classified_error_hint_defaults_none():
    ce = ClassifiedError(error_class=ErrorClass.TRANSIENT, code="X", message="m")
    assert ce.hint is None


def test_classified_error_is_frozen():
    ce = ClassifiedError(error_class=ErrorClass.TRANSIENT, code="X", message="m")
    with pytest.raises(FrozenInstanceError):
        ce.code = "Y"  # type: ignore[misc]


# ---- EventLine dataclass -------------------------------------------------


def test_event_line_fields():
    el = EventLine(
        phase=Phase.PROVISIONING,
        status="log",
        message="creating EC2 instance",
        extra={"step": 3},
    )
    assert el.phase == Phase.PROVISIONING
    assert el.status == "log"
    assert el.extra == {"step": 3}


def test_event_line_extra_defaults_none():
    el = EventLine(phase=Phase.PREFLIGHT, status="running", message="checking creds")
    assert el.extra is None


# ---- PhaseResult dataclass -----------------------------------------------


def test_phase_result_defaults():
    pr = PhaseResult(next_phase=Phase.PROVISIONING)
    assert pr.next_phase == Phase.PROVISIONING
    assert pr.outputs is None
    assert pr.event is None


def test_phase_result_with_outputs_and_event():
    pr = PhaseResult(
        next_phase=Phase.BOOTSTRAPPING,
        outputs={"instance_id": "i-abc"},
        event=EventLine(Phase.PROVISIONING, "succeeded", "ec2 running"),
    )
    assert pr.outputs == {"instance_id": "i-abc"}
    assert pr.event is not None and pr.event.status == "succeeded"


def test_phase_result_next_phase_none_means_stay():
    """A handler returning next_phase=None means 'stay in current phase'
    (used by transient retries that should schedule a backoff)."""
    pr = PhaseResult(next_phase=None)
    assert pr.next_phase is None


# ---- ProvisioningJob model -----------------------------------------------


def _now() -> datetime:
    return datetime(2026, 5, 28, 12, 0, 0, tzinfo=timezone.utc)


def test_provisioning_job_roundtrip_from_row():
    """ProvisioningJob.from_row(asyncpg.Record-like dict) -> Pydantic instance."""
    row = {
        "id": uuid.uuid4(),
        "node_id": uuid.uuid4(),
        "pool_id": uuid.uuid4(),
        "org_id": "org-1",
        "provider": "aws",
        "spec": {"instance_type": "g6.xlarge"},
        "phase": "preflight",
        "attempt_count": 1,
        "next_attempt_after": None,
        "last_error_code": None,
        "last_error_message": None,
        "last_error_hint": None,
        "error_class": None,
        "lease_holder": "inferia-app-1234-host",
        "lease_expires_at": _now(),
        "pulumi_stack_outputs": None,
        "created_at": _now(),
        "updated_at": _now(),
    }
    job = ProvisioningJob.from_row(row)
    assert job.phase == Phase.PREFLIGHT
    assert job.spec == {"instance_type": "g6.xlarge"}
    assert job.attempt_count == 1


def test_provisioning_job_from_row_decodes_jsonb_string_spec():
    """asyncpg returns jsonb columns as raw JSON strings when no codec is
    registered. from_row must decode `spec` / `pulumi_stack_outputs`
    strings so Pydantic's dict validators accept them (regression: the
    reconciler crashed with 'Input should be a valid dictionary' on every
    claim because spec arrived as a str)."""
    row = {
        "id": uuid.uuid4(),
        "node_id": uuid.uuid4(),
        "pool_id": uuid.uuid4(),
        "org_id": "org-1",
        "provider": "aws",
        "spec": '{"instance_type": "g6.xlarge", "region": "us-east-1"}',
        "phase": "preflight",
        "attempt_count": 0,
        "next_attempt_after": None,
        "last_error_code": None,
        "last_error_message": None,
        "last_error_hint": None,
        "error_class": None,
        "lease_holder": None,
        "lease_expires_at": None,
        "pulumi_stack_outputs": '{"ami_id": "ami-123"}',
        "created_at": _now(),
        "updated_at": _now(),
    }
    job = ProvisioningJob.from_row(row)
    assert job.spec == {"instance_type": "g6.xlarge", "region": "us-east-1"}
    assert job.pulumi_stack_outputs == {"ami_id": "ami-123"}


def test_provisioning_job_from_row_handles_empty_and_bad_json():
    """Empty-string / malformed jsonb degrade to None/{} without raising."""
    base = {
        "id": uuid.uuid4(), "node_id": uuid.uuid4(), "pool_id": uuid.uuid4(),
        "org_id": "o", "provider": "aws", "phase": "preflight",
        "attempt_count": 0, "next_attempt_after": None,
        "last_error_code": None, "last_error_message": None,
        "last_error_hint": None, "error_class": None, "lease_holder": None,
        "lease_expires_at": None, "created_at": _now(), "updated_at": _now(),
    }
    job = ProvisioningJob.from_row({**base, "spec": "", "pulumi_stack_outputs": "not json"})
    assert job.spec == {}
    assert job.pulumi_stack_outputs is None


def test_provisioning_job_phase_is_terminal_proxy():
    row = _row_with(phase="ready")
    assert ProvisioningJob.from_row(row).phase.is_terminal


def test_provisioning_job_error_fields_optional():
    row = _row_with(phase="pending")
    job = ProvisioningJob.from_row(row)
    assert job.last_error_code is None
    assert job.error_class is None


def _row_with(**overrides):
    row = {
        "id": uuid.uuid4(),
        "node_id": uuid.uuid4(),
        "pool_id": uuid.uuid4(),
        "org_id": "org-1",
        "provider": "aws",
        "spec": {},
        "phase": "pending",
        "attempt_count": 0,
        "next_attempt_after": None,
        "last_error_code": None,
        "last_error_message": None,
        "last_error_hint": None,
        "error_class": None,
        "lease_holder": None,
        "lease_expires_at": None,
        "pulumi_stack_outputs": None,
        "created_at": _now(),
        "updated_at": _now(),
    }
    row.update(overrides)
    return row

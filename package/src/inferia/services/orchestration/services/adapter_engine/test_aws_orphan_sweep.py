"""Tests for the boto3 tag-based orphan/duplicate EC2 sweep backstop.

These tests inject a fake (MagicMock) boto3 EC2 client by monkey-patching
the module's client factory seam (``_ec2_client``), so no real AWS call is
ever made — mirroring the established injectable-client style in
``test_aws_deprovision.py`` (which patches ``ADAPTER_REGISTRY``) and the
``_boto3_*`` seam used in ``adapters/pulumi/credentials.py``.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from inferia.services.orchestration.services.adapter_engine import (
    aws_orphan_sweep,
)


# ---------------------------------------------------------------------------
# Helpers — build a scripted describe_instances response.
# ---------------------------------------------------------------------------


def _describe_response(instance_ids: list[str]) -> dict:
    """Return a minimal describe_instances payload listing the given ids,
    spread across a couple of reservations to mimic the real shape."""
    reservations = [
        {"Instances": [{"InstanceId": iid, "State": {"Name": "running"}}]}
        for iid in instance_ids
    ]
    return {"Reservations": reservations}


def _fake_client(*, describe_response: dict | None = None,
                 describe_raises: BaseException | None = None,
                 terminate_raises: BaseException | None = None) -> MagicMock:
    client = MagicMock(name="ec2_client")
    if describe_raises is not None:
        client.describe_instances.side_effect = describe_raises
    else:
        client.describe_instances.return_value = describe_response or _describe_response([])
    if terminate_raises is not None:
        client.terminate_instances.side_effect = terminate_raises
    else:
        client.terminate_instances.return_value = {"TerminatingInstances": []}
    return client


def _patch_client(client: MagicMock):
    """Patch the module's client factory + credential resolution so no real
    AWS / DB access happens."""
    return patch.multiple(
        aws_orphan_sweep,
        _ec2_client=MagicMock(return_value=client),
        _resolve_aws_creds=MagicMock(return_value={
            "aws_access_key_id": "AKIA-fake",
            "aws_secret_access_key": "secret-fake",
        }),
    )


# ---------------------------------------------------------------------------
# Node sweep — terminates every tagged instance, incl. ones pulumi state
# would not know about (the duplicate double-launch leak).
# ---------------------------------------------------------------------------


def test_sweep_node_instances_terminates_all_tagged() -> None:
    client = _fake_client(describe_response=_describe_response(["i-aaa", "i-bbb"]))
    with _patch_client(client):
        out = aws_orphan_sweep.sweep_node_instances("node-1", "us-east-1")

    assert out == ["i-aaa", "i-bbb"]
    # describe filtered by the per-NODE tag + the live states.
    _, kwargs = client.describe_instances.call_args
    filters = kwargs["Filters"]
    assert {"Name": "tag:InferiaNodeId", "Values": ["node-1"]} in filters
    assert any(
        f["Name"] == "instance-state-name"
        and set(f["Values"]) == {"pending", "running", "stopping", "stopped"}
        for f in filters
    )
    # Both ids were terminated in a single call.
    client.terminate_instances.assert_called_once_with(InstanceIds=["i-aaa", "i-bbb"])


# ---------------------------------------------------------------------------
# Pool sweep — filters by the per-POOL tag.
# ---------------------------------------------------------------------------


def test_sweep_pool_instances_filters_by_pool_tag() -> None:
    client = _fake_client(describe_response=_describe_response(["i-ccc", "i-ddd"]))
    with _patch_client(client):
        out = aws_orphan_sweep.sweep_pool_instances("pool-9", "eu-west-1")

    assert out == ["i-ccc", "i-ddd"]
    _, kwargs = client.describe_instances.call_args
    filters = kwargs["Filters"]
    assert {"Name": "tag:InferiaPoolId", "Values": ["pool-9"]} in filters
    client.terminate_instances.assert_called_once_with(InstanceIds=["i-ccc", "i-ddd"])


# ---------------------------------------------------------------------------
# Empty describe → no terminate, returns [].
# ---------------------------------------------------------------------------


def test_empty_describe_does_not_terminate() -> None:
    client = _fake_client(describe_response=_describe_response([]))
    with _patch_client(client):
        out = aws_orphan_sweep.sweep_node_instances("node-empty", "us-east-1")

    assert out == []
    client.terminate_instances.assert_not_called()


# ---------------------------------------------------------------------------
# Best-effort: a raising AWS call returns [] and logs (never raises).
# ---------------------------------------------------------------------------


def test_describe_error_is_best_effort_returns_empty(caplog) -> None:
    client = _fake_client(describe_raises=RuntimeError("aws boom"))
    with _patch_client(client), caplog.at_level("WARNING"):
        out = aws_orphan_sweep.sweep_node_instances("node-1", "us-east-1")

    assert out == []
    client.terminate_instances.assert_not_called()
    assert any("aws boom" in r.getMessage() or "sweep" in r.getMessage().lower()
               for r in caplog.records)


def test_terminate_error_is_best_effort_returns_empty(caplog) -> None:
    client = _fake_client(
        describe_response=_describe_response(["i-eee"]),
        terminate_raises=RuntimeError("terminate boom"),
    )
    with _patch_client(client), caplog.at_level("WARNING"):
        out = aws_orphan_sweep.sweep_pool_instances("pool-1", "us-east-1")

    assert out == []
    # Mirror the describe-error test: a raising terminate is logged as a
    # best-effort WARNING (never swallowed silently).
    assert any("terminate boom" in r.getMessage() or "sweep" in r.getMessage().lower()
               for r in caplog.records)


# ---------------------------------------------------------------------------
# Success path emits a clear log line listing terminated ids.
# ---------------------------------------------------------------------------


def test_success_logs_terminated_ids(caplog) -> None:
    client = _fake_client(describe_response=_describe_response(["i-aaa", "i-bbb"]))
    with _patch_client(client), caplog.at_level("INFO"):
        aws_orphan_sweep.sweep_node_instances("node-1", "us-east-1")

    joined = " ".join(r.getMessage() for r in caplog.records)
    assert "i-aaa" in joined and "i-bbb" in joined

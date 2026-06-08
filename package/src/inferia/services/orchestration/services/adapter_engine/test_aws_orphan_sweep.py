"""Tests for the boto3 tag-based orphan/duplicate EC2 sweep backstop.

These tests inject a fake (MagicMock) boto3 EC2 client by monkey-patching
the module's client factory seam (``_ec2_client``), so no real AWS call is
ever made — mirroring the established injectable-client style in
``test_aws_deprovision.py`` (which patches ``ADAPTER_REGISTRY``) and the
``_boto3_*`` seam used in ``adapters/pulumi/credentials.py``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

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


# ---------------------------------------------------------------------------
# Credential resolution — the sweep must authenticate the SAME way the
# Pulumi destroy path does (resolve_aws_env(load_providers_config())), NOT
# via the ambient boto3 default chain (the CP container has none → the
# backstop would silently no-op). These tests exercise the REAL
# _resolve_aws_creds / _ec2_client (patching only resolve_aws_env,
# load_providers_config, and boto3) so a regression to os.environ / the
# default chain is caught.
# ---------------------------------------------------------------------------


def test_resolve_aws_creds_uses_resolve_aws_env() -> None:
    """_resolve_aws_creds loads ProvidersConfig (the destroy-path source) and
    maps resolve_aws_env's output into boto3 client kwargs, incl. the session
    token when present."""
    fake_env = {
        "AWS_ACCESS_KEY_ID": "AKIA-resolved",
        "AWS_SECRET_ACCESS_KEY": "secret-resolved",
        "AWS_DEFAULT_REGION": "us-east-1",
        "AWS_SESSION_TOKEN": "tok-resolved",
    }
    sentinel_cfg = object()
    with patch.object(
        aws_orphan_sweep, "_resolve_aws_creds",
        wraps=aws_orphan_sweep._resolve_aws_creds,
    ), patch(
        "inferia.services.orchestration.services.adapter_engine.adapters.pulumi"
        ".pulumi_aws_adapter.load_providers_config",
        new=AsyncMock(return_value=sentinel_cfg),
    ), patch(
        "inferia.services.orchestration.services.adapter_engine.adapters.pulumi"
        ".credentials.resolve_aws_env",
        return_value=fake_env,
    ) as mock_resolve:
        creds = aws_orphan_sweep._resolve_aws_creds()

    # The config the destroy path loads is what got handed to resolve_aws_env.
    mock_resolve.assert_called_once_with(sentinel_cfg)
    assert creds == {
        "aws_access_key_id": "AKIA-resolved",
        "aws_secret_access_key": "secret-resolved",
        "aws_session_token": "tok-resolved",
    }


def test_resolve_aws_creds_omits_session_token_when_absent() -> None:
    fake_env = {
        "AWS_ACCESS_KEY_ID": "AKIA-resolved",
        "AWS_SECRET_ACCESS_KEY": "secret-resolved",
        "AWS_DEFAULT_REGION": "us-east-1",
    }
    with patch(
        "inferia.services.orchestration.services.adapter_engine.adapters.pulumi"
        ".pulumi_aws_adapter.load_providers_config",
        new=AsyncMock(return_value=object()),
    ), patch(
        "inferia.services.orchestration.services.adapter_engine.adapters.pulumi"
        ".credentials.resolve_aws_env",
        return_value=fake_env,
    ):
        creds = aws_orphan_sweep._resolve_aws_creds()

    assert creds == {
        "aws_access_key_id": "AKIA-resolved",
        "aws_secret_access_key": "secret-resolved",
    }
    assert "aws_session_token" not in creds


def test_resolve_aws_creds_returns_empty_on_missing_credentials() -> None:
    """When ProvidersConfig has no AWS creds, resolve_aws_env raises
    MissingCredentialsError and _resolve_aws_creds returns {} (so the caller
    bails instead of falling back to the empty ambient chain)."""
    from inferia.services.orchestration.services.adapter_engine.adapters.pulumi.credentials import (
        MissingCredentialsError,
    )

    with patch(
        "inferia.services.orchestration.services.adapter_engine.adapters.pulumi"
        ".pulumi_aws_adapter.load_providers_config",
        new=AsyncMock(return_value=object()),
    ), patch(
        "inferia.services.orchestration.services.adapter_engine.adapters.pulumi"
        ".credentials.resolve_aws_env",
        side_effect=MissingCredentialsError("access_key_id is required"),
    ):
        creds = aws_orphan_sweep._resolve_aws_creds()

    assert creds == {}


def test_ec2_client_passes_resolved_creds_to_boto3() -> None:
    """_ec2_client forwards the resolved creds + region to boto3.client —
    NOT relying on the ambient default chain."""
    fake_boto3 = MagicMock(name="boto3")
    creds = {
        "aws_access_key_id": "AKIA-resolved",
        "aws_secret_access_key": "secret-resolved",
        "aws_session_token": "tok-resolved",
    }
    with patch.dict("sys.modules", {"boto3": fake_boto3}):
        aws_orphan_sweep._ec2_client("eu-west-1", creds=creds)

    fake_boto3.client.assert_called_once_with(
        "ec2",
        region_name="eu-west-1",
        aws_access_key_id="AKIA-resolved",
        aws_secret_access_key="secret-resolved",
        aws_session_token="tok-resolved",
    )


def test_sweep_builds_client_with_resolved_creds() -> None:
    """End-to-end through sweep_node_instances: the real _resolve_aws_creds
    runs (resolve_aws_env patched to return creds), and the boto3 EC2 client
    factory receives those creds explicitly — proving the sweep authenticates
    via the destroy-path creds rather than the ambient chain."""
    fake_env = {
        "AWS_ACCESS_KEY_ID": "AKIA-resolved",
        "AWS_SECRET_ACCESS_KEY": "secret-resolved",
        "AWS_DEFAULT_REGION": "us-east-1",
    }
    client = _fake_client(describe_response=_describe_response(["i-zzz"]))
    captured: dict = {}

    def _fake_ec2_client(region, *, creds):
        captured["region"] = region
        captured["creds"] = creds
        return client

    with patch(
        "inferia.services.orchestration.services.adapter_engine.adapters.pulumi"
        ".pulumi_aws_adapter.load_providers_config",
        new=AsyncMock(return_value=object()),
    ), patch(
        "inferia.services.orchestration.services.adapter_engine.adapters.pulumi"
        ".credentials.resolve_aws_env",
        return_value=fake_env,
    ), patch.object(aws_orphan_sweep, "_ec2_client", side_effect=_fake_ec2_client):
        out = aws_orphan_sweep.sweep_node_instances("node-1", "us-east-1")

    assert out == ["i-zzz"]
    assert captured["region"] == "us-east-1"
    assert captured["creds"] == {
        "aws_access_key_id": "AKIA-resolved",
        "aws_secret_access_key": "secret-resolved",
    }
    client.describe_instances.assert_called_once()


# ---------------------------------------------------------------------------
# No-creds case — when no AWS creds are configured the sweep logs a clear
# (no-creds-specific) WARNING and returns [] WITHOUT building a client or
# calling terminate. Distinct from the "no instances" INFO line.
# ---------------------------------------------------------------------------


def test_no_creds_warns_and_returns_empty_without_terminate(caplog) -> None:
    ec2_factory = MagicMock(name="_ec2_client")
    with patch.object(
        aws_orphan_sweep, "_resolve_aws_creds", MagicMock(return_value={}),
    ), patch.object(
        aws_orphan_sweep, "_ec2_client", ec2_factory,
    ), caplog.at_level("WARNING"):
        out = aws_orphan_sweep.sweep_node_instances("node-1", "us-east-1")

    assert out == []
    # No client built, no terminate attempted.
    ec2_factory.assert_not_called()
    # WARNING distinguishes "no creds" from "no instances".
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert any("no AWS credentials" in r.getMessage() for r in warnings)


def test_no_creds_message_distinct_from_no_instances(caplog) -> None:
    """The no-creds WARNING must NOT read like the empty-describe INFO line."""
    with patch.object(
        aws_orphan_sweep, "_resolve_aws_creds", MagicMock(return_value={}),
    ), caplog.at_level("INFO"):
        aws_orphan_sweep.sweep_pool_instances("pool-1", "us-east-1")

    msgs = [r.getMessage() for r in caplog.records]
    assert any("no AWS credentials" in m for m in msgs)
    # The "no live instances" wording is reserved for an authenticated,
    # empty describe — it must not appear when we never authenticated.
    assert not any("no live instances" in m for m in msgs)


def test_resolve_creds_failure_is_best_effort(caplog) -> None:
    """A failure resolving creds (e.g. DB down) is logged and yields [] —
    never raises, never builds a client."""
    ec2_factory = MagicMock(name="_ec2_client")
    with patch.object(
        aws_orphan_sweep, "_resolve_aws_creds",
        MagicMock(side_effect=RuntimeError("db down")),
    ), patch.object(
        aws_orphan_sweep, "_ec2_client", ec2_factory,
    ), caplog.at_level("WARNING"):
        out = aws_orphan_sweep.sweep_node_instances("node-1", "us-east-1")

    assert out == []
    ec2_factory.assert_not_called()
    assert any("db down" in r.getMessage() or "resolve AWS credentials" in r.getMessage()
               for r in caplog.records)

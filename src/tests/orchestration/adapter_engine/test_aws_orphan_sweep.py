"""Tests for the boto3 tag-based orphan/duplicate EC2 sweep backstop.

These tests inject a fake (MagicMock) boto3 EC2 client by monkey-patching
the module's client factory seam (``_ec2_client``), so no real AWS call is
ever made — mirroring the established injectable-client style in
``test_aws_deprovision.py`` (which patches ``ADAPTER_REGISTRY``) and the
``_boto3_*`` seam used in ``adapters/pulumi/credentials.py``.

Creds are NO LONGER resolved inside the sweep (that previously did
``asyncio.run(load_providers_config())`` from a ``to_thread`` worker thread,
which crashed cross-loop and silently no-op'd in production). The async
caller resolves them on the main loop and passes ``aws_env`` IN, so every
test hands a fake ``aws_env`` dict directly.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.orchestration.adapter_engine import (
    aws_orphan_sweep,
)


# A fake ``aws_env`` exactly as ``resolve_aws_env(cfg)`` would return it
# (no session token — the standard long-lived-key config path).
_FAKE_AWS_ENV = {
    "AWS_ACCESS_KEY_ID": "AKIA-fake",
    "AWS_SECRET_ACCESS_KEY": "secret-fake",
    "AWS_DEFAULT_REGION": "us-east-1",
}


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
    """Patch the module's client factory so no real AWS access happens.

    Creds are now supplied by the caller via ``aws_env`` (passed into the
    sweep call), so there is nothing else to patch here.
    """
    return patch.object(
        aws_orphan_sweep, "_ec2_client", MagicMock(return_value=client),
    )


# ---------------------------------------------------------------------------
# Node sweep — terminates every tagged instance, incl. ones pulumi state
# would not know about (the duplicate double-launch leak).
# ---------------------------------------------------------------------------


def test_sweep_node_instances_terminates_all_tagged() -> None:
    client = _fake_client(describe_response=_describe_response(["i-aaa", "i-bbb"]))
    with _patch_client(client):
        out = aws_orphan_sweep.sweep_node_instances(
            "node-1", "us-east-1", _FAKE_AWS_ENV,
        )

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
        out = aws_orphan_sweep.sweep_pool_instances(
            "pool-9", "eu-west-1", _FAKE_AWS_ENV,
        )

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
        out = aws_orphan_sweep.sweep_node_instances(
            "node-empty", "us-east-1", _FAKE_AWS_ENV,
        )

    assert out == []
    client.terminate_instances.assert_not_called()


# ---------------------------------------------------------------------------
# Best-effort: a raising AWS call returns [] and logs (never raises).
# ---------------------------------------------------------------------------


def test_describe_error_is_best_effort_returns_empty(caplog) -> None:
    client = _fake_client(describe_raises=RuntimeError("aws boom"))
    with _patch_client(client), caplog.at_level("WARNING"):
        out = aws_orphan_sweep.sweep_node_instances(
            "node-1", "us-east-1", _FAKE_AWS_ENV,
        )

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
        out = aws_orphan_sweep.sweep_pool_instances(
            "pool-1", "us-east-1", _FAKE_AWS_ENV,
        )

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
        aws_orphan_sweep.sweep_node_instances("node-1", "us-east-1", _FAKE_AWS_ENV)

    joined = " ".join(r.getMessage() for r in caplog.records)
    assert "i-aaa" in joined and "i-bbb" in joined


# ---------------------------------------------------------------------------
# Creds come from the PASSED aws_env (NOT the ambient default chain, and NOT
# resolved inside the sweep). These prove the boto3 EC2 client is built from
# the dict the async caller resolved via resolve_aws_env(cfg) and handed in.
# ---------------------------------------------------------------------------


def test_ec2_client_passes_resolved_creds_to_boto3() -> None:
    """_ec2_client forwards the mapped creds + region to boto3.client —
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


def test_creds_from_aws_env_maps_keys_incl_session_token() -> None:
    """_creds_from_aws_env maps resolve_aws_env's output into boto3 client
    kwargs, forwarding the session token when present."""
    env = {
        "AWS_ACCESS_KEY_ID": "AKIA-resolved",
        "AWS_SECRET_ACCESS_KEY": "secret-resolved",
        "AWS_DEFAULT_REGION": "us-east-1",
        "AWS_SESSION_TOKEN": "tok-resolved",
    }
    assert aws_orphan_sweep._creds_from_aws_env(env) == {
        "aws_access_key_id": "AKIA-resolved",
        "aws_secret_access_key": "secret-resolved",
        "aws_session_token": "tok-resolved",
    }


def test_creds_from_aws_env_omits_session_token_when_absent() -> None:
    assert aws_orphan_sweep._creds_from_aws_env(_FAKE_AWS_ENV) == {
        "aws_access_key_id": "AKIA-fake",
        "aws_secret_access_key": "secret-fake",
    }


def test_sweep_builds_client_with_passed_aws_env_creds() -> None:
    """End-to-end through sweep_node_instances: the boto3 EC2 client factory
    receives the creds mapped from the passed aws_env — proving the sweep
    authenticates via the caller-resolved creds rather than the ambient chain."""
    client = _fake_client(describe_response=_describe_response(["i-zzz"]))
    captured: dict = {}

    def _fake_ec2_client(region, *, creds):
        captured["region"] = region
        captured["creds"] = creds
        return client

    with patch.object(aws_orphan_sweep, "_ec2_client", side_effect=_fake_ec2_client):
        out = aws_orphan_sweep.sweep_node_instances(
            "node-1", "us-east-1", _FAKE_AWS_ENV,
        )

    assert out == ["i-zzz"]
    assert captured["region"] == "us-east-1"
    assert captured["creds"] == {
        "aws_access_key_id": "AKIA-fake",
        "aws_secret_access_key": "secret-fake",
    }
    client.describe_instances.assert_called_once()


# ---------------------------------------------------------------------------
# No-creds case — when the caller could not resolve creds (aws_env is None /
# empty) the sweep logs a clear (no-creds-specific) WARNING and returns []
# WITHOUT building a client or calling terminate. Distinct from "no instances".
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("aws_env", [None, {}])
def test_no_creds_warns_and_returns_empty_without_client(aws_env, caplog) -> None:
    ec2_factory = MagicMock(name="_ec2_client")
    with patch.object(
        aws_orphan_sweep, "_ec2_client", ec2_factory,
    ), caplog.at_level("WARNING"):
        out = aws_orphan_sweep.sweep_node_instances("node-1", "us-east-1", aws_env)

    assert out == []
    # No client built, no terminate attempted.
    ec2_factory.assert_not_called()
    # WARNING distinguishes "no creds" from "no instances".
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert any("no AWS credentials" in r.getMessage() for r in warnings)


def test_no_creds_message_distinct_from_no_instances(caplog) -> None:
    """The no-creds WARNING must NOT read like the empty-describe INFO line."""
    with caplog.at_level("INFO"):
        aws_orphan_sweep.sweep_pool_instances("pool-1", "us-east-1", None)

    msgs = [r.getMessage() for r in caplog.records]
    assert any("no AWS credentials" in m for m in msgs)
    # The "no live instances" wording is reserved for an authenticated,
    # empty describe — it must not appear when we never authenticated.
    assert not any("no live instances" in m for m in msgs)


def test_default_aws_env_is_none_no_creds(caplog) -> None:
    """Calling without an aws_env arg (default None) must NOT touch the
    ambient chain — it logs no-creds and returns []."""
    ec2_factory = MagicMock(name="_ec2_client")
    with patch.object(
        aws_orphan_sweep, "_ec2_client", ec2_factory,
    ), caplog.at_level("WARNING"):
        out = aws_orphan_sweep.sweep_node_instances("node-1", "us-east-1")

    assert out == []
    ec2_factory.assert_not_called()


def test_malformed_aws_env_is_best_effort(caplog) -> None:
    """A non-empty but malformed aws_env (missing AWS_ACCESS_KEY_ID) is logged
    and yields [] — never raises, never builds a client."""
    ec2_factory = MagicMock(name="_ec2_client")
    with patch.object(
        aws_orphan_sweep, "_ec2_client", ec2_factory,
    ), caplog.at_level("WARNING"):
        out = aws_orphan_sweep.sweep_node_instances(
            "node-1", "us-east-1", {"WRONG_KEY": "x"},
        )

    assert out == []
    ec2_factory.assert_not_called()
    assert any("malformed AWS creds" in r.getMessage() for r in caplog.records)


# ---------------------------------------------------------------------------
# resolve_sweep_aws_env — the ASYNC helper the caller awaits on its main loop.
# It mirrors the Pulumi destroy path: resolve_aws_env(load_providers_config()).
# Best-effort: MissingCredentialsError / any failure → None (so the caller
# still runs the sweep, which logs no-creds and returns []).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_sweep_aws_env_uses_destroy_path() -> None:
    fake_env = {
        "AWS_ACCESS_KEY_ID": "AKIA-resolved",
        "AWS_SECRET_ACCESS_KEY": "secret-resolved",
        "AWS_DEFAULT_REGION": "us-east-1",
    }
    sentinel_cfg = object()
    with patch(
        "providers.pulumi"
        ".pulumi_aws_adapter.load_providers_config",
        new=AsyncMock(return_value=sentinel_cfg),
    ), patch(
        "providers.pulumi"
        ".credentials.resolve_aws_env",
        return_value=fake_env,
    ) as mock_resolve:
        out = await aws_orphan_sweep.resolve_sweep_aws_env()

    # The config the destroy path loads is what got handed to resolve_aws_env.
    mock_resolve.assert_called_once_with(sentinel_cfg)
    assert out == fake_env


@pytest.mark.asyncio
async def test_resolve_sweep_aws_env_returns_none_on_missing_creds(caplog) -> None:
    from providers.pulumi.credentials import (
        MissingCredentialsError,
    )

    with patch(
        "providers.pulumi"
        ".pulumi_aws_adapter.load_providers_config",
        new=AsyncMock(return_value=object()),
    ), patch(
        "providers.pulumi"
        ".credentials.resolve_aws_env",
        side_effect=MissingCredentialsError("access_key_id is required"),
    ), caplog.at_level("WARNING"):
        out = await aws_orphan_sweep.resolve_sweep_aws_env()

    assert out is None
    assert any("no AWS credentials configured" in r.getMessage()
               for r in caplog.records)


@pytest.mark.asyncio
async def test_resolve_sweep_aws_env_returns_none_on_db_failure(caplog) -> None:
    """A failure loading ProvidersConfig (e.g. DB down) is logged and yields
    None — never raises, never breaks the caller's teardown flow."""
    with patch(
        "providers.pulumi"
        ".pulumi_aws_adapter.load_providers_config",
        new=AsyncMock(side_effect=RuntimeError("db down")),
    ), caplog.at_level("WARNING"):
        out = await aws_orphan_sweep.resolve_sweep_aws_env()

    assert out is None
    assert any("failed to resolve AWS credentials" in r.getMessage()
               for r in caplog.records)


# ---------------------------------------------------------------------------
# sweep_stale_builders — reclaim engine-AMI builder instances leaked by a
# CP crash mid-bake (the bake normally terminates its builder in a finally).
# ---------------------------------------------------------------------------

from datetime import datetime, timedelta, timezone

from services.orchestration.adapter_engine import aws_orphan_sweep as sweep

AWS_ENV = {"AWS_ACCESS_KEY_ID": "k", "AWS_SECRET_ACCESS_KEY": "s"}


class _FakeEC2Builders:
    def __init__(self, instances):
        self._instances = instances
        self.terminated = []

    def describe_instances(self, **kw):
        self.describe_kw = kw
        return {"Reservations": [{"Instances": self._instances}]}

    def terminate_instances(self, **kw):
        self.terminated.extend(kw["InstanceIds"])


def test_sweep_stale_builders_terminates_only_old(monkeypatch):
    now = datetime(2026, 6, 9, 12, 0, tzinfo=timezone.utc)
    insts = [
        {"InstanceId": "i-old", "LaunchTime": now - timedelta(minutes=45)},
        {"InstanceId": "i-fresh", "LaunchTime": now - timedelta(minutes=5)},
    ]
    fake = _FakeEC2Builders(insts)
    monkeypatch.setattr(sweep, "_ec2_client", lambda region, **kw: fake)
    out = sweep.sweep_stale_builders("us-east-1", AWS_ENV, older_than_min=30, now=now)
    assert out == ["i-old"]
    assert fake.terminated == ["i-old"]
    # The sweep must filter by the builder tag — otherwise it would terminate
    # unrelated instances.
    filters = fake.describe_kw["Filters"]
    assert any(f["Name"] == "tag:inferia:engine-ami-builder" and f["Values"] == ["true"] for f in filters)


def test_sweep_stale_builders_no_creds():
    assert sweep.sweep_stale_builders("us-east-1", None) == []


def test_sweep_stale_builders_none_stale(monkeypatch):
    now = datetime(2026, 6, 9, 12, 0, tzinfo=timezone.utc)
    fake = _FakeEC2Builders([{"InstanceId": "i-fresh", "LaunchTime": now - timedelta(minutes=2)}])
    monkeypatch.setattr(sweep, "_ec2_client", lambda region, **kw: fake)
    out = sweep.sweep_stale_builders("us-east-1", AWS_ENV, older_than_min=30, now=now)
    assert out == []
    assert fake.terminated == []


def test_sweep_stale_builders_describe_error(monkeypatch):
    class _Boom:
        def describe_instances(self, **kw):
            raise RuntimeError("AccessDenied")
    monkeypatch.setattr(sweep, "_ec2_client", lambda region, **kw: _Boom())
    assert sweep.sweep_stale_builders("us-east-1", AWS_ENV) == []


def test_sweep_stale_builders_naive_launchtime_does_not_raise(monkeypatch):
    # A tz-naive LaunchTime would raise on comparison; best-effort must swallow.
    fake = _FakeEC2Builders([{"InstanceId": "i-x", "LaunchTime": datetime(2026, 6, 9, 11, 0)}])
    monkeypatch.setattr(sweep, "_ec2_client", lambda region, **kw: fake)
    out = sweep.sweep_stale_builders("us-east-1", AWS_ENV, now=datetime(2026, 6, 9, 12, 0, tzinfo=timezone.utc))
    assert out == []  # comparison TypeError swallowed → best-effort []

"""Tests for run_pulumi_up_sync — the pure sync function that replaces
provision_node + _provision_async.

Pre-T10, this module also tested PulumiAWSAdapter.provision_node /
_provision_async / provision_cluster. Those methods were deleted in T10
because their DB-write side effects move to the reconciler in T15+.
Only tests for the pure run_pulumi_up_sync helper and the surviving
adapter methods (wait_for_ready, deprovision_node, discover_resources,
get_logs) remain.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pulumi.automation import CommandResult, ConcurrentUpdateError

from inferia.services.orchestration.services.adapter_engine.adapters.pulumi.pulumi_aws_adapter import (
    PulumiAWSAdapter,
    ProvisionError,
    StackOutputs,
    run_pulumi_destroy_sync,
    run_pulumi_up_sync,
)
from inferia.services.orchestration.services.provisioning.errors import (
    AMINotFoundError,
    InvalidCredentialsError,
    PulumiCliMissingError,
    PulumiTransientError,
)

_MAKE_STACK = (
    "inferia.services.orchestration.services.adapter_engine."
    "adapters.pulumi.pulumi_aws_adapter._make_stack"
)


def _stale_lock_error() -> ConcurrentUpdateError:
    """Build a real ConcurrentUpdateError as pulumi.automation raises it
    when the file-backend lock is held (e.g. a prior pulumi process died
    mid-run under host memory pressure)."""
    return ConcurrentUpdateError(
        CommandResult(
            stdout="",
            stderr=(
                "error: the stack is currently locked by 1 lock(s). Either wait "
                "for the other process(es) to end or delete the lock file with "
                "'pulumi cancel'."
            ),
            code=255,
        )
    )


def _aws_cfg_dict(**kw):
    base = {
        "access_key_id": "AKIAREALKEY1234XYZ8",
        "secret_access_key": "real-secret-not-masked-1234567",
        "region": "us-east-1",
        "subnet_id": "subnet-0123456789abcdef0",
        "security_group_ids": ["sg-0123456789abcdef0"],
        "ami_id": "ami-0123456789abcdef0",
        "root_volume_gb": 200,
    }
    base.update(kw)
    return base


@pytest.fixture
def fake_db():
    db = MagicMock()
    db.execute = AsyncMock(return_value="INSERT 0 1")
    db.fetchrow = AsyncMock(return_value=None)
    return db


@pytest.fixture
def aws_config():
    from inferia.services.api_gateway.config import (
        AWSConfig,
        CloudConfig,
        ProvidersConfig,
    )

    return ProvidersConfig(cloud=CloudConfig(aws=AWSConfig(**_aws_cfg_dict())))


# ---------------------------------------------------------------------------
# T10: run_pulumi_up_sync — the pure synchronous Pulumi-up function.
# ---------------------------------------------------------------------------


def test_run_pulumi_up_sync_returns_stack_outputs_on_success():
    """A successful stack.up() returns a StackOutputs dataclass."""
    fake_stack = MagicMock()
    fake_stack.up.return_value = MagicMock(outputs={
        "instance_id": MagicMock(value="i-abc"),
        "public_dns": MagicMock(value="ec2-1-2-3-4.compute-1.amazonaws.com"),
        "region": MagicMock(value="us-east-1"),
        "ami_id": MagicMock(value="ami-deadbeef"),
    })
    with patch(
        "inferia.services.orchestration.services.adapter_engine."
        "adapters.pulumi.pulumi_aws_adapter._make_stack",
        return_value=fake_stack,
    ):
        out = run_pulumi_up_sync(
            stack_name="org-pool-node",
            program=lambda: None,
            env={"AWS_ACCESS_KEY_ID": "x", "AWS_SECRET_ACCESS_KEY": "y"},
        )
    assert isinstance(out, StackOutputs)
    assert out.instance_id == "i-abc"
    assert out.region == "us-east-1"


def test_run_pulumi_up_sync_raises_pulumi_cli_missing_on_filenotfounderror():
    with patch(
        "inferia.services.orchestration.services.adapter_engine."
        "adapters.pulumi.pulumi_aws_adapter._make_stack",
        side_effect=FileNotFoundError("[Errno 2] No such file or directory: 'pulumi'"),
    ):
        with pytest.raises(PulumiCliMissingError):
            run_pulumi_up_sync(
                stack_name="s", program=lambda: None, env={},
            )


def test_run_pulumi_up_sync_raises_invalid_credentials_on_auth_failure():
    """Pulumi up surfacing an AWS AuthFailure becomes InvalidCredentialsError."""
    fake_stack = MagicMock()
    err = Exception(
        "operation failed: AuthFailure: AWS was not able to validate "
        "the provided access credentials"
    )
    fake_stack.up.side_effect = err
    with patch(
        "inferia.services.orchestration.services.adapter_engine."
        "adapters.pulumi.pulumi_aws_adapter._make_stack",
        return_value=fake_stack,
    ):
        with pytest.raises(InvalidCredentialsError):
            run_pulumi_up_sync(
                stack_name="s", program=lambda: None, env={},
            )


def test_run_pulumi_up_sync_raises_ami_not_found():
    fake_stack = MagicMock()
    fake_stack.up.side_effect = Exception(
        "InvalidAMIID.NotFound: The image id '[ami-deadbeef]' does not exist"
    )
    with patch(
        "inferia.services.orchestration.services.adapter_engine."
        "adapters.pulumi.pulumi_aws_adapter._make_stack",
        return_value=fake_stack,
    ):
        with pytest.raises(AMINotFoundError):
            run_pulumi_up_sync(
                stack_name="s", program=lambda: None, env={},
            )


def test_run_pulumi_up_sync_uses_local_workspace_with_env():
    """Env vars are passed into local_workspace_opts so Pulumi inherits them."""
    fake_stack = MagicMock()
    fake_stack.up.return_value = MagicMock(outputs={
        "instance_id": MagicMock(value="i-x"),
    })
    with patch(
        "inferia.services.orchestration.services.adapter_engine."
        "adapters.pulumi.pulumi_aws_adapter._make_stack",
        return_value=fake_stack,
    ) as mk:
        run_pulumi_up_sync(
            stack_name="s",
            program=lambda: None,
            env={"AWS_ACCESS_KEY_ID": "K", "AWS_SECRET_ACCESS_KEY": "S"},
        )
    kwargs = mk.call_args.kwargs
    assert kwargs["env"] == {"AWS_ACCESS_KEY_ID": "K", "AWS_SECRET_ACCESS_KEY": "S"}


def test_run_pulumi_up_sync_passes_project_name_and_state_dir():
    """_make_stack receives project_name + state_dir; project name comes
    from PROJECT_NAME constant by default. This is the Critical-1 path
    surfaced in T10 review: ``auto.create_or_select_stack`` requires a
    project_name for inline programs, and the reconciler must pass a
    persistent state_dir so deprovision_node can later reopen the same
    stack."""
    fake_stack = MagicMock()
    fake_stack.up.return_value = MagicMock(outputs={
        "instance_id": MagicMock(value="i-x"),
    })
    with patch(
        "inferia.services.orchestration.services.adapter_engine."
        "adapters.pulumi.pulumi_aws_adapter._make_stack",
        return_value=fake_stack,
    ) as mk:
        run_pulumi_up_sync(
            stack_name="s",
            program=lambda: None,
            env={"AWS_ACCESS_KEY_ID": "K", "AWS_SECRET_ACCESS_KEY": "S"},
            state_dir="/var/lib/inferia/pulumi",
        )
    kwargs = mk.call_args.kwargs
    assert kwargs["project_name"] == "inferia-aws"
    assert kwargs["state_dir"] == "/var/lib/inferia/pulumi"


def test_make_stack_merges_pulumi_env_from_process_env(monkeypatch):
    """If the caller doesn't pass PULUMI_BACKEND_URL/etc, _make_stack
    pulls them from the process environment (Critical-2 from T10 review).
    Without this merge the stack would land on Pulumi cloud (or fail)
    and the state wouldn't be reusable across pulumi up + deprovision_node.

    Patches LocalWorkspaceOptions + create_or_select_stack directly so
    no real pulumi.automation imports happen (the auto module is already
    available via pulumi.automation at module import time)."""
    monkeypatch.setenv("PULUMI_BACKEND_URL", "file:///tmp/pulumi-state")
    monkeypatch.setenv("PULUMI_CONFIG_PASSPHRASE", "test-pass")
    monkeypatch.setenv("PULUMI_HOME", "/tmp/pulumi-home")

    captured: dict = {}

    def fake_local_workspace_options(**kw):
        captured["lwo"] = kw
        return MagicMock()

    def fake_create_or_select_stack(**kw):
        captured["cos"] = kw
        return MagicMock()

    def fake_project_settings(**kw):
        captured["ps"] = kw
        return MagicMock()

    from inferia.services.orchestration.services.adapter_engine.adapters.pulumi import (
        pulumi_aws_adapter as adapter_mod,
    )
    with patch.object(
        adapter_mod, "pulumi", MagicMock()
    ), patch(
        "pulumi.automation.LocalWorkspaceOptions", side_effect=fake_local_workspace_options
    ), patch(
        "pulumi.automation.create_or_select_stack", side_effect=fake_create_or_select_stack
    ), patch(
        "pulumi.automation.ProjectSettings", side_effect=fake_project_settings
    ):
        adapter_mod._make_stack(
            stack_name="s",
            program=lambda: None,
            env={"AWS_ACCESS_KEY_ID": "K"},
        )

    # AWS creds preserved; Pulumi backend env merged in from process env.
    env_vars = captured["lwo"]["env_vars"]
    assert env_vars["AWS_ACCESS_KEY_ID"] == "K"
    assert env_vars["PULUMI_BACKEND_URL"] == "file:///tmp/pulumi-state"
    assert env_vars["PULUMI_CONFIG_PASSPHRASE"] == "test-pass"
    assert env_vars["PULUMI_HOME"] == "/tmp/pulumi-home"
    # project_name + workspace project_settings carry "inferia-aws".
    assert captured["cos"]["project_name"] == "inferia-aws"
    assert captured["ps"]["name"] == "inferia-aws"


def test_make_stack_caller_pulumi_env_wins_over_process_env(monkeypatch):
    """If the caller explicitly puts a PULUMI_* var in `env`, _make_stack
    must NOT overwrite it with the process-env value. The merge is a
    fallback for missing keys, not a force-set."""
    monkeypatch.setenv("PULUMI_BACKEND_URL", "file:///process/pulumi-state")
    captured: dict = {}

    def fake_local_workspace_options(**kw):
        captured["lwo"] = kw
        return MagicMock()

    from inferia.services.orchestration.services.adapter_engine.adapters.pulumi import (
        pulumi_aws_adapter as adapter_mod,
    )
    with patch(
        "pulumi.automation.LocalWorkspaceOptions", side_effect=fake_local_workspace_options
    ), patch(
        "pulumi.automation.create_or_select_stack", return_value=MagicMock()
    ), patch(
        "pulumi.automation.ProjectSettings", return_value=MagicMock()
    ):
        adapter_mod._make_stack(
            stack_name="s",
            program=lambda: None,
            env={
                "AWS_ACCESS_KEY_ID": "K",
                "PULUMI_BACKEND_URL": "file:///caller/pulumi-state",
            },
        )

    assert captured["lwo"]["env_vars"]["PULUMI_BACKEND_URL"] == "file:///caller/pulumi-state"


# ---------------------------------------------------------------------------
# Edge cases for the run_pulumi_up_sync error-classification heuristic.
# These cover throttling (-> PulumiTransientError) and unknown errors
# (-> re-raise so the reconciler's classifier sees them as UNCLASSIFIED).
# ---------------------------------------------------------------------------


def test_run_pulumi_up_sync_raises_transient_on_throttling():
    fake_stack = MagicMock()
    fake_stack.up.side_effect = Exception(
        "RequestLimitExceeded: Request rate limit exceeded"
    )
    with patch(
        "inferia.services.orchestration.services.adapter_engine."
        "adapters.pulumi.pulumi_aws_adapter._make_stack",
        return_value=fake_stack,
    ):
        with pytest.raises(PulumiTransientError):
            run_pulumi_up_sync(
                stack_name="s", program=lambda: None, env={},
            )


def test_run_pulumi_up_sync_raises_transient_on_throttling_keyword():
    fake_stack = MagicMock()
    fake_stack.up.side_effect = Exception(
        "operation failed: Throttling: rate exceeded"
    )
    with patch(
        "inferia.services.orchestration.services.adapter_engine."
        "adapters.pulumi.pulumi_aws_adapter._make_stack",
        return_value=fake_stack,
    ):
        with pytest.raises(PulumiTransientError):
            run_pulumi_up_sync(
                stack_name="s", program=lambda: None, env={},
            )


def test_run_pulumi_up_sync_reraises_unknown_error():
    """Errors that don't match any heuristic pattern propagate unchanged so
    the reconciler's classifier maps them to UNCLASSIFIED PERMANENT."""
    fake_stack = MagicMock()
    fake_stack.up.side_effect = RuntimeError("some unfamiliar pulumi failure")
    with patch(
        "inferia.services.orchestration.services.adapter_engine."
        "adapters.pulumi.pulumi_aws_adapter._make_stack",
        return_value=fake_stack,
    ):
        with pytest.raises(RuntimeError, match="some unfamiliar pulumi failure"):
            run_pulumi_up_sync(
                stack_name="s", program=lambda: None, env={},
            )


def test_run_pulumi_up_sync_propagates_provisioning_errors():
    """A ProvisioningError raised from inside stack.up() is propagated
    unchanged — the classifier (already running upstream) is the source of
    truth for that case."""
    fake_stack = MagicMock()
    fake_stack.up.side_effect = AMINotFoundError("ami already classified")
    with patch(
        "inferia.services.orchestration.services.adapter_engine."
        "adapters.pulumi.pulumi_aws_adapter._make_stack",
        return_value=fake_stack,
    ):
        with pytest.raises(AMINotFoundError, match="ami already classified"):
            run_pulumi_up_sync(
                stack_name="s", program=lambda: None, env={},
            )


# ---------------------------------------------------------------------------
# StackOutputs dataclass: handles missing keys and raw (non-Reference) values.
# ---------------------------------------------------------------------------


def test_stack_outputs_from_pulumi_outputs_handles_missing_keys():
    out = StackOutputs.from_pulumi_outputs({})
    assert out.instance_id is None
    assert out.public_dns is None
    assert out.region is None
    assert out.ami_id is None


def test_stack_outputs_from_pulumi_outputs_handles_raw_values():
    """Outputs may be raw scalars (no `.value` attribute) in unit tests
    that bypass pulumi.automation.OutputValue. Verify the helper accepts
    both shapes."""
    out = StackOutputs.from_pulumi_outputs({
        "instance_id": "i-raw",
        "public_dns": MagicMock(value="dns-wrapped"),
    })
    assert out.instance_id == "i-raw"
    assert out.public_dns == "dns-wrapped"
    assert out.region is None
    assert out.ami_id is None


# ---------------------------------------------------------------------------
# Surviving adapter methods — wait_for_ready, deprovision_node, discover,
# get_logs. These don't write to compute_pools.metadata; the reconciler
# owns lifecycle.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_wait_for_ready_polls_until_inventory_ready(fake_db, aws_config, tmp_path):
    fake_db.fetchrow = AsyncMock(side_effect=[
        {"state": "pending"},
        {"state": "pending"},
        {"state": "ready"},
    ])
    adapter = PulumiAWSAdapter(db=fake_db, state_dir=str(tmp_path))
    result = await adapter.wait_for_ready(
        provider_instance_id="boot-1",
        timeout=30,
        poll_interval=0.01,
    )
    assert result == "ready"
    assert fake_db.fetchrow.call_count == 3


@pytest.mark.asyncio
async def test_wait_for_ready_timeout_destroys_stack(fake_db, aws_config, tmp_path):
    fake_db.fetchrow = AsyncMock(return_value={"state": "pending"})
    fake_stack = MagicMock()
    fake_stack.destroy = MagicMock(return_value=None)
    adapter = PulumiAWSAdapter(db=fake_db, state_dir=str(tmp_path))
    with patch.object(adapter, "_select_stack", return_value=fake_stack), patch(
        "inferia.services.orchestration.services.adapter_engine.adapters.pulumi.pulumi_aws_adapter."
        "load_providers_config",
        return_value=aws_config,
    ):
        with pytest.raises(ProvisionError):
            await adapter.wait_for_ready(
                provider_instance_id="boot-1",
                timeout=0.05,
                poll_interval=0.01,
            )
    fake_stack.destroy.assert_called_once()


@pytest.mark.asyncio
async def test_deprovision_node_destroys_stack(fake_db, aws_config, tmp_path):
    fake_stack = MagicMock()
    fake_stack.destroy = MagicMock(return_value=None)
    fake_stack.workspace = MagicMock()
    fake_stack.workspace.remove_stack = MagicMock()
    adapter = PulumiAWSAdapter(db=fake_db, state_dir=str(tmp_path))
    with patch.object(adapter, "_select_stack", return_value=fake_stack), patch(
        "inferia.services.orchestration.services.adapter_engine.adapters.pulumi.pulumi_aws_adapter."
        "load_providers_config",
        return_value=aws_config,
    ):
        await adapter.deprovision_node(provider_instance_id="00000000-0000-0000-0000-000000000001")
    fake_stack.destroy.assert_called_once()


@pytest.mark.asyncio
async def test_discover_resources_normalizes_output(fake_db, aws_config, tmp_path):
    mock_ec2 = MagicMock()
    mock_ec2.describe_instance_types.return_value = {
        "InstanceTypes": [
            {
                "InstanceType": "g5.xlarge",
                "VCpuInfo": {"DefaultVCpus": 4},
                "MemoryInfo": {"SizeInMiB": 16384},
                "GpuInfo": {"Gpus": [{"Name": "A10G", "Count": 1,
                                       "Manufacturer": "NVIDIA",
                                       "MemoryInfo": {"SizeInMiB": 24576}}]},
            },
            {
                "InstanceType": "m5.large",
                "VCpuInfo": {"DefaultVCpus": 2},
                "MemoryInfo": {"SizeInMiB": 8192},
            },
        ]
    }
    with patch("boto3.client", return_value=mock_ec2), patch(
        "inferia.services.orchestration.services.adapter_engine.adapters.pulumi.pulumi_aws_adapter."
        "load_providers_config",
        return_value=aws_config,
    ):
        adapter = PulumiAWSAdapter(db=fake_db, state_dir=str(tmp_path))
        out = await adapter.discover_resources(region="us-east-1")
    by_type = {r["provider_resource_id"]: r for r in out}
    assert by_type["g5.xlarge"]["gpu_vendor"] == "nvidia"
    assert by_type["g5.xlarge"]["gpu_memory_gb"] == 24
    assert by_type["m5.large"]["gpu_vendor"] == "none"
    assert by_type["m5.large"]["gpu_count"] == 0


@pytest.mark.asyncio
async def test_get_logs_returns_console_output(fake_db, aws_config, tmp_path):
    mock_ec2 = MagicMock()
    mock_ec2.get_console_output.return_value = {"Output": "line1\nline2\n"}
    with patch("boto3.client", return_value=mock_ec2), patch(
        "inferia.services.orchestration.services.adapter_engine.adapters.pulumi.pulumi_aws_adapter."
        "load_providers_config",
        return_value=aws_config,
    ):
        adapter = PulumiAWSAdapter(db=fake_db, state_dir=str(tmp_path))
        logs = await adapter.get_logs(provider_instance_id="i-abc")
    assert logs == {"logs": ["line1", "line2"]}


@pytest.mark.asyncio
async def test_get_logs_error_returns_empty(fake_db, aws_config, tmp_path):
    mock_ec2 = MagicMock()
    mock_ec2.get_console_output.side_effect = Exception("AccessDenied")
    with patch("boto3.client", return_value=mock_ec2), patch(
        "inferia.services.orchestration.services.adapter_engine.adapters.pulumi.pulumi_aws_adapter."
        "load_providers_config",
        return_value=aws_config,
    ):
        adapter = PulumiAWSAdapter(db=fake_db, state_dir=str(tmp_path))
        logs = await adapter.get_logs(provider_instance_id="i-abc")
    assert logs == {"logs": []}


# ---------------------------------------------------------------------------
# Stale pulumi-lock recovery: a prior pulumi process dying mid-run under host
# memory pressure leaves a file-backend lock. The next reconciler retry hits a
# ConcurrentUpdateError. Both run_pulumi_up_sync and run_pulumi_destroy_sync
# must clear the stale lock with stack.cancel() and retry exactly ONCE; a
# persistent lock must propagate (no infinite loop). Per-node stacks are never
# operated on concurrently, so a lock seen here is always stale → cancel() is
# safe. (Root-caused live: died-process locks were looping the reconciler so
# the node never purged + the pool never finalized.)
# ---------------------------------------------------------------------------


def test_run_pulumi_destroy_sync_recovers_from_stale_lock():
    """destroy hits a stale lock once, then succeeds on retry: cancel() runs
    once, destroy runs twice, the call returns success."""
    fake_stack = MagicMock()
    fake_stack.destroy.side_effect = [_stale_lock_error(), None]
    fake_stack.cancel = MagicMock()
    with patch(_MAKE_STACK, return_value=fake_stack):
        run_pulumi_destroy_sync(stack_name="org-pool-node", program=lambda: None, env={})
    assert fake_stack.cancel.call_count == 1
    assert fake_stack.destroy.call_count == 2


def test_run_pulumi_up_sync_recovers_from_stale_lock():
    """up hits a stale lock once, then succeeds on retry: cancel() runs once,
    up runs twice, outputs are returned."""
    fake_stack = MagicMock()
    fake_stack.up.side_effect = [
        _stale_lock_error(),
        MagicMock(outputs={"instance_id": MagicMock(value="i-recovered")}),
    ]
    fake_stack.cancel = MagicMock()
    with patch(_MAKE_STACK, return_value=fake_stack):
        out = run_pulumi_up_sync(stack_name="org-pool-node", program=lambda: None, env={})
    assert fake_stack.cancel.call_count == 1
    assert fake_stack.up.call_count == 2
    assert isinstance(out, StackOutputs)
    assert out.instance_id == "i-recovered"


def test_run_pulumi_destroy_sync_persistent_lock_propagates():
    """A lock that is STILL held after cancel() (destroy raises
    ConcurrentUpdateError both times) propagates — no infinite loop. cancel()
    runs once, destroy runs exactly twice."""
    fake_stack = MagicMock()
    fake_stack.destroy.side_effect = [_stale_lock_error(), _stale_lock_error()]
    fake_stack.cancel = MagicMock()
    with patch(_MAKE_STACK, return_value=fake_stack):
        with pytest.raises(ConcurrentUpdateError):
            run_pulumi_destroy_sync(stack_name="org-pool-node", program=lambda: None, env={})
    assert fake_stack.cancel.call_count == 1
    assert fake_stack.destroy.call_count == 2


def test_run_pulumi_up_sync_persistent_lock_propagates():
    """Same as above for up(): a still-locked stack propagates the
    ConcurrentUpdateError after exactly one cancel()+retry."""
    fake_stack = MagicMock()
    fake_stack.up.side_effect = [_stale_lock_error(), _stale_lock_error()]
    fake_stack.cancel = MagicMock()
    with patch(_MAKE_STACK, return_value=fake_stack):
        with pytest.raises(ConcurrentUpdateError):
            run_pulumi_up_sync(stack_name="org-pool-node", program=lambda: None, env={})
    assert fake_stack.cancel.call_count == 1
    assert fake_stack.up.call_count == 2


def test_run_pulumi_destroy_sync_happy_path_no_cancel():
    """No lock: destroy succeeds first try → cancel() is NEVER called
    (regression guard — existing behavior must be unchanged)."""
    fake_stack = MagicMock()
    fake_stack.destroy.return_value = None
    fake_stack.cancel = MagicMock()
    with patch(_MAKE_STACK, return_value=fake_stack):
        run_pulumi_destroy_sync(stack_name="org-pool-node", program=lambda: None, env={})
    fake_stack.cancel.assert_not_called()
    assert fake_stack.destroy.call_count == 1


def test_run_pulumi_up_sync_happy_path_no_cancel():
    """No lock: up succeeds first try → cancel() is NEVER called."""
    fake_stack = MagicMock()
    fake_stack.up.return_value = MagicMock(outputs={"instance_id": MagicMock(value="i-ok")})
    fake_stack.cancel = MagicMock()
    with patch(_MAKE_STACK, return_value=fake_stack):
        out = run_pulumi_up_sync(stack_name="org-pool-node", program=lambda: None, env={})
    fake_stack.cancel.assert_not_called()
    assert fake_stack.up.call_count == 1
    assert out.instance_id == "i-ok"


def test_run_pulumi_destroy_sync_no_stack_named_bypasses_recovery():
    """A generic 'no stack named' error (NOT a ConcurrentUpdateError) keeps
    the existing idempotency: destroy returns success without ever calling
    cancel(). Confirms the stale-lock recovery only triggers on the lock
    exception type and never swallows the no-stack path."""
    fake_stack = MagicMock()
    fake_stack.destroy.side_effect = Exception("no stack named 'org-pool-node' found")
    fake_stack.cancel = MagicMock()
    with patch(_MAKE_STACK, return_value=fake_stack):
        run_pulumi_destroy_sync(stack_name="org-pool-node", program=lambda: None, env={})
    fake_stack.cancel.assert_not_called()
    assert fake_stack.destroy.call_count == 1

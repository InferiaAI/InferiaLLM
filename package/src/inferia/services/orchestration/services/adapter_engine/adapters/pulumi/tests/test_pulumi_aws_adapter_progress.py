"""Verify PulumiAWSAdapter emits the 8-phase progress event sequence.

Mocks `pulumi.automation.create_or_select_stack` and `stack.up` so the
test never touches AWS or the Pulumi runtime. The adapter calls
progress_writer.write_async at phase boundaries and forwards an
on_event callback into stack.up that we can verify is wired.
"""
from __future__ import annotations
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from inferia.services.orchestration.services.adapter_engine.adapters.pulumi.pulumi_aws_adapter import (
    PulumiAWSAdapter,
    ProvisionError,
)


class RecordingWriter:
    def __init__(self):
        self.calls = []  # list of (phase, status, message)
    async def write_async(self, phase, status, message=None):
        self.calls.append((phase, status, message))
    def write(self, phase, status, message=None):
        self.calls.append((phase, status, message))


def _ok_outputs():
    # The adapter's _extract_output expects values with a .value attr
    # OR raw values. Mix both shapes for coverage.
    return {
        "instance_id": SimpleNamespace(value="i-abc123"),
        "public_dns":  SimpleNamespace(value="ec2-1.amazonaws.com"),
        "private_ip":  SimpleNamespace(value="10.0.0.5"),
    }


def _fake_providers_config():
    from inferia.services.api_gateway.config import ProvidersConfig, CloudConfig, AWSConfig
    return ProvidersConfig(cloud=CloudConfig(aws=AWSConfig(
        access_key_id="AKIA_TEST",
        secret_access_key="secret_test",
        region="us-east-1",
        ami_id="ami-stub",  # skip ami lookup
    )))


@pytest.mark.asyncio
async def test_provision_node_emits_eight_phase_sequence_on_success():
    adapter = PulumiAWSAdapter(state_dir="/tmp/pulumi-test")
    writer = RecordingWriter()
    pool = str(uuid4())
    org = str(uuid4())

    fake_stack = MagicMock()
    up_result = MagicMock()
    up_result.outputs = _ok_outputs()
    fake_stack.up.return_value = up_result

    db = AsyncMock()
    db.execute = AsyncMock()
    adapter._db = db

    with patch.object(adapter, "ensure_state_dir"), \
         patch("inferia.services.orchestration.services.adapter_engine.adapters.pulumi.pulumi_aws_adapter.load_providers_config",
               new=AsyncMock(return_value=_fake_providers_config())), \
         patch("inferia.services.orchestration.services.adapter_engine.adapters.pulumi.pulumi_aws_adapter.mint_bootstrap_token",
               new=AsyncMock(return_value=("tok", uuid4()))), \
         patch("inferia.services.orchestration.services.adapter_engine.adapters.pulumi.pulumi_aws_adapter.pulumi.automation.create_or_select_stack",
               return_value=fake_stack):
        result = await adapter.provision_node(
            provider_resource_id="t3.micro",
            pool_id=pool,
            org_id=org,
            progress_writer=writer,
        )

    assert result["lifecycle_state"] == "provisioning"
    # The background task needs the loop to drain
    for _ in range(50):
        await asyncio.sleep(0.02)
        if any(c[0] == "worker_bootstrap" for c in writer.calls):
            break
    phases = [c[0] for c in writer.calls]
    # First six events (synchronous part) are prepare(running, succeeded) +
    # pulumi_init(running, succeeded) + pulumi_up(running) — ami_lookup is
    # skipped because AMI is pinned in the providers config.
    assert phases[:5] == ["prepare", "prepare",
                          "pulumi_init", "pulumi_init",
                          "pulumi_up"]
    statuses_for_each = {p: [s for ph, s, _ in writer.calls if ph == p]
                         for p in set(phases)}
    assert statuses_for_each["prepare"] == ["running", "succeeded"]
    assert statuses_for_each["pulumi_init"] == ["running", "succeeded"]
    # ami_lookup never emits when AMI is pinned
    assert "ami_lookup" not in statuses_for_each

    # Verify the placeholder-swap UPDATE on success ran with the right bindings.
    update_calls = db.execute.await_args_list
    swap_call = next((c for c in update_calls
                      if "UPDATE compute_inventory" in (c.args[0] if c.args else "")
                      and "placeholder:%" in (c.args[0] if c.args else "")), None)
    assert swap_call is not None, "expected placeholder-swap UPDATE on success"
    # bindings are positional: instance_id, public_dns, pool_id
    assert swap_call.args[1] == "i-abc123"
    assert swap_call.args[2] == "ec2-1.amazonaws.com"


@pytest.mark.asyncio
async def test_provision_node_writes_ami_lookup_phase_when_ami_not_pinned():
    adapter = PulumiAWSAdapter(state_dir="/tmp/pulumi-test")
    adapter._db = AsyncMock()
    writer = RecordingWriter()
    fake_stack = MagicMock()
    up_result = MagicMock(); up_result.outputs = _ok_outputs()
    fake_stack.up.return_value = up_result
    cfg = _fake_providers_config()
    cfg.cloud.aws.ami_id = None  # force lookup

    with patch.object(adapter, "ensure_state_dir"), \
         patch("inferia.services.orchestration.services.adapter_engine.adapters.pulumi.pulumi_aws_adapter.load_providers_config",
               new=AsyncMock(return_value=cfg)), \
         patch("inferia.services.orchestration.services.adapter_engine.adapters.pulumi.pulumi_aws_adapter.mint_bootstrap_token",
               new=AsyncMock(return_value=("tok", uuid4()))), \
         patch("inferia.services.orchestration.services.adapter_engine.adapters.pulumi.pulumi_aws_adapter.latest_dlami_ami",
               return_value="ami-found"), \
         patch("inferia.services.orchestration.services.adapter_engine.adapters.pulumi.pulumi_aws_adapter.pulumi.automation.create_or_select_stack",
               return_value=fake_stack):
        await adapter.provision_node(
            provider_resource_id="t3.micro",
            pool_id=str(uuid4()), org_id=str(uuid4()),
            progress_writer=writer,
        )
    phase_statuses = [(p, s) for p, s, _ in writer.calls]
    assert ("ami_lookup", "running") in phase_statuses
    assert ("ami_lookup", "succeeded") in phase_statuses


@pytest.mark.asyncio
async def test_pulumi_up_failure_emits_failed_status():
    adapter = PulumiAWSAdapter(state_dir="/tmp/pulumi-test")
    writer = RecordingWriter()
    fake_stack = MagicMock()
    fake_stack.up.side_effect = RuntimeError("aws: insufficient capacity")

    db = AsyncMock(); db.execute = AsyncMock(); adapter._db = db

    with patch.object(adapter, "ensure_state_dir"), \
         patch("inferia.services.orchestration.services.adapter_engine.adapters.pulumi.pulumi_aws_adapter.load_providers_config",
               new=AsyncMock(return_value=_fake_providers_config())), \
         patch("inferia.services.orchestration.services.adapter_engine.adapters.pulumi.pulumi_aws_adapter.mint_bootstrap_token",
               new=AsyncMock(return_value=("tok", uuid4()))), \
         patch("inferia.services.orchestration.services.adapter_engine.adapters.pulumi.pulumi_aws_adapter.pulumi.automation.create_or_select_stack",
               return_value=fake_stack):
        await adapter.provision_node(
            provider_resource_id="t3.micro",
            pool_id=str(uuid4()), org_id=str(uuid4()),
            progress_writer=writer,
        )
    for _ in range(50):
        await asyncio.sleep(0.02)
        if any(s == "failed" for _, s, _ in writer.calls):
            break
    fails = [c for c in writer.calls if c[1] == "failed"]
    assert len(fails) == 1
    assert fails[0][0] == "pulumi_up"
    assert "insufficient capacity" in (fails[0][2] or "")

    # Verify the failure path marks placeholder inventory rows as terminated.
    update_calls = db.execute.await_args_list
    terminated_call = next((c for c in update_calls
                            if "UPDATE compute_inventory" in (c.args[0] if c.args else "")
                            and "'terminated'" in (c.args[0] if c.args else "")), None)
    assert terminated_call is not None, "expected placeholder-terminated UPDATE on failure"
    import json as _json
    failure_meta = _json.loads(terminated_call.args[1])
    assert "insufficient capacity" in failure_meta["failure_reason"]


@pytest.mark.asyncio
async def test_no_writer_provided_falls_back_to_noop():
    """The lazy-deploy path (no progress_writer) must still work."""
    adapter = PulumiAWSAdapter(state_dir="/tmp/pulumi-test")
    adapter._db = AsyncMock()
    fake_stack = MagicMock()
    fake_stack.up.return_value = MagicMock(outputs=_ok_outputs())
    with patch.object(adapter, "ensure_state_dir"), \
         patch("inferia.services.orchestration.services.adapter_engine.adapters.pulumi.pulumi_aws_adapter.load_providers_config",
               new=AsyncMock(return_value=_fake_providers_config())), \
         patch("inferia.services.orchestration.services.adapter_engine.adapters.pulumi.pulumi_aws_adapter.mint_bootstrap_token",
               new=AsyncMock(return_value=("tok", uuid4()))), \
         patch("inferia.services.orchestration.services.adapter_engine.adapters.pulumi.pulumi_aws_adapter.pulumi.automation.create_or_select_stack",
               return_value=fake_stack):
        result = await adapter.provision_node(
            provider_resource_id="t3.micro",
            pool_id=str(uuid4()), org_id=str(uuid4()),
        )
    assert result["lifecycle_state"] == "provisioning"


@pytest.mark.asyncio
async def test_pulumi_up_called_with_on_event_callback():
    adapter = PulumiAWSAdapter(state_dir="/tmp/pulumi-test")
    adapter._db = AsyncMock()
    writer = RecordingWriter()
    fake_stack = MagicMock()
    fake_stack.up.return_value = MagicMock(outputs=_ok_outputs())
    with patch.object(adapter, "ensure_state_dir"), \
         patch("inferia.services.orchestration.services.adapter_engine.adapters.pulumi.pulumi_aws_adapter.load_providers_config",
               new=AsyncMock(return_value=_fake_providers_config())), \
         patch("inferia.services.orchestration.services.adapter_engine.adapters.pulumi.pulumi_aws_adapter.mint_bootstrap_token",
               new=AsyncMock(return_value=("tok", uuid4()))), \
         patch("inferia.services.orchestration.services.adapter_engine.adapters.pulumi.pulumi_aws_adapter.pulumi.automation.create_or_select_stack",
               return_value=fake_stack):
        await adapter.provision_node(
            provider_resource_id="t3.micro",
            pool_id=str(uuid4()), org_id=str(uuid4()),
            progress_writer=writer,
        )
        for _ in range(20):
            await asyncio.sleep(0.02)
            if fake_stack.up.called:
                break
    args, kwargs = fake_stack.up.call_args
    assert "on_event" in kwargs
    assert callable(kwargs["on_event"])


@pytest.mark.asyncio
async def test_provision_node_invalid_metadata_emits_prepare_failed():
    """Invalid AWS pool metadata short-circuits provision_node with
    `prepare/failed` written and a ProvisionError raised. No pulumi
    work is started."""
    adapter = PulumiAWSAdapter(state_dir="/tmp/pulumi-test")
    adapter._db = AsyncMock()
    writer = RecordingWriter()

    with patch.object(adapter, "ensure_state_dir"), \
         patch("inferia.services.orchestration.services.adapter_engine.adapters.pulumi.pulumi_aws_adapter.load_providers_config",
               new=AsyncMock(return_value=_fake_providers_config())), \
         patch("inferia.services.orchestration.services.adapter_engine.adapters.pulumi.pulumi_aws_adapter.mint_bootstrap_token",
               new=AsyncMock(return_value=("tok", uuid4()))), \
         patch("inferia.services.orchestration.services.adapter_engine.adapters.pulumi.pulumi_aws_adapter.pulumi.automation.create_or_select_stack"):
        with pytest.raises(ProvisionError, match="invalid AWS metadata"):
            await adapter.provision_node(
                provider_resource_id="t3.micro",
                pool_id=str(uuid4()), org_id=str(uuid4()),
                metadata={"subnet_id": 12345},  # subnet_id must be a string
                progress_writer=writer,
            )
    phases = [(p, s) for p, s, _ in writer.calls]
    assert ("prepare", "running") in phases
    assert ("prepare", "failed") in phases
    # No ami_lookup or pulumi_init events when prepare failed early.
    assert not any(p == "ami_lookup" for p, _ in phases)
    assert not any(p == "pulumi_init" for p, _ in phases)


@pytest.mark.asyncio
async def test_provision_node_ami_lookup_failure_emits_ami_lookup_failed():
    adapter = PulumiAWSAdapter(state_dir="/tmp/pulumi-test")
    adapter._db = AsyncMock()
    writer = RecordingWriter()
    cfg = _fake_providers_config()
    cfg.cloud.aws.ami_id = None  # force lookup

    from inferia.services.orchestration.services.adapter_engine.adapters.pulumi.ami import (
        AMILookupError,
    )

    with patch.object(adapter, "ensure_state_dir"), \
         patch("inferia.services.orchestration.services.adapter_engine.adapters.pulumi.pulumi_aws_adapter.load_providers_config",
               new=AsyncMock(return_value=cfg)), \
         patch("inferia.services.orchestration.services.adapter_engine.adapters.pulumi.pulumi_aws_adapter.mint_bootstrap_token",
               new=AsyncMock(return_value=("tok", uuid4()))), \
         patch("inferia.services.orchestration.services.adapter_engine.adapters.pulumi.pulumi_aws_adapter.latest_dlami_ami",
               side_effect=AMILookupError("no images found")):
        with pytest.raises(ProvisionError, match="AMI lookup failed"):
            await adapter.provision_node(
                provider_resource_id="t3.micro",
                pool_id=str(uuid4()), org_id=str(uuid4()),
                progress_writer=writer,
            )
    phase_statuses = [(p, s) for p, s, _ in writer.calls]
    assert ("ami_lookup", "running") in phase_statuses
    assert ("ami_lookup", "failed") in phase_statuses
    # Downstream phases must NOT fire on AMI failure.
    assert not any(p == "pulumi_init" for p, _ in phase_statuses)
    assert not any(p == "pulumi_up" for p, _ in phase_statuses)


@pytest.mark.asyncio
async def test_on_event_callback_classifies_known_pulumi_events():
    """The sync _on_event passed to stack.up classifies events of each
    known kind and forwards to writer.write with status='log'."""
    from types import SimpleNamespace
    adapter = PulumiAWSAdapter(state_dir="/tmp/pulumi-test")
    adapter._db = AsyncMock()
    writer = RecordingWriter()

    captured_callback = {}

    def _fake_up(*, on_event):
        captured_callback["fn"] = on_event
        return MagicMock(outputs=_ok_outputs())

    fake_stack = MagicMock()
    fake_stack.up.side_effect = _fake_up

    with patch.object(adapter, "ensure_state_dir"), \
         patch("inferia.services.orchestration.services.adapter_engine.adapters.pulumi.pulumi_aws_adapter.load_providers_config",
               new=AsyncMock(return_value=_fake_providers_config())), \
         patch("inferia.services.orchestration.services.adapter_engine.adapters.pulumi.pulumi_aws_adapter.mint_bootstrap_token",
               new=AsyncMock(return_value=("tok", uuid4()))), \
         patch("inferia.services.orchestration.services.adapter_engine.adapters.pulumi.pulumi_aws_adapter.pulumi.automation.create_or_select_stack",
               return_value=fake_stack):
        await adapter.provision_node(
            provider_resource_id="t3.micro",
            pool_id=str(uuid4()), org_id=str(uuid4()),
            progress_writer=writer,
        )
    # Wait for the background task to call up()
    for _ in range(30):
        await asyncio.sleep(0.02)
        if "fn" in captured_callback:
            break
    callback = captured_callback["fn"]
    assert callable(callback)

    # Drive the callback with a fake resource_pre_event.
    fake_event = SimpleNamespace(
        resource_pre_event=SimpleNamespace(
            metadata=SimpleNamespace(op="create", urn="urn:aws:ec2:Instance::test"),
        ),
        res_outputs_event=None,
        diagnostic_event=None,
        summary_event=None,
    )
    pre_count = len([c for c in writer.calls if c[0] == "pulumi_up" and c[1] == "log"])
    callback(fake_event)
    post_count = len([c for c in writer.calls if c[0] == "pulumi_up" and c[1] == "log"])
    assert post_count == pre_count + 1
    # Last logged message should mention the kind we classified.
    last_log = [c for c in writer.calls if c[0] == "pulumi_up" and c[1] == "log"][-1]
    assert "resource_pre_event" in (last_log[2] or "")

    # Also exercise the except-swallow branch (lines 329-330): pass an event
    # whose attribute access raises, so the try body explodes and the bare
    # `except Exception: pass` is hit.  The callback must not propagate.
    class _BadEvent:
        @property
        def resource_pre_event(self):
            raise RuntimeError("intentional error for coverage")
        res_outputs_event = None
        diagnostic_event = None
        summary_event = None

    callback(_BadEvent())  # must not raise


@pytest.mark.asyncio
async def test_pulumi_up_failure_calls_stack_destroy():
    """On pulumi up failure the adapter must run stack.destroy() so AWS
    resources don't leak. Verify by asserting the mock's destroy call.
    Also covers the `except Exception` handler (lines 393-394) when
    destroy itself raises."""
    adapter = PulumiAWSAdapter(state_dir="/tmp/pulumi-test")
    adapter._db = AsyncMock()
    writer = RecordingWriter()
    fake_stack = MagicMock()
    fake_stack.up.side_effect = RuntimeError("aws: throttled")
    # Make destroy raise so we also hit the except-swallow on lines 393-394.
    fake_stack.destroy.side_effect = RuntimeError("destroy also failed")

    with patch.object(adapter, "ensure_state_dir"), \
         patch("inferia.services.orchestration.services.adapter_engine.adapters.pulumi.pulumi_aws_adapter.load_providers_config",
               new=AsyncMock(return_value=_fake_providers_config())), \
         patch("inferia.services.orchestration.services.adapter_engine.adapters.pulumi.pulumi_aws_adapter.mint_bootstrap_token",
               new=AsyncMock(return_value=("tok", uuid4()))), \
         patch("inferia.services.orchestration.services.adapter_engine.adapters.pulumi.pulumi_aws_adapter.pulumi.automation.create_or_select_stack",
               return_value=fake_stack):
        await adapter.provision_node(
            provider_resource_id="t3.micro",
            pool_id=str(uuid4()), org_id=str(uuid4()),
            progress_writer=writer,
        )
    # Drain background task — wait until pulumi_up/failed is written,
    # which only happens after destroy has been attempted.
    for _ in range(100):
        await asyncio.sleep(0.02)
        if any(s == "failed" for _, s, _ in writer.calls):
            break
    assert fake_stack.destroy.called, "stack.destroy must be called on pulumi_up failure"


# ---------------------------------------------------------------------------
# GPU-name → EC2 instance-type mapping (defensive layer)
# ---------------------------------------------------------------------------

def test_resolve_instance_type_passthrough_for_real_instance():
    from inferia.services.orchestration.services.adapter_engine.adapters.pulumi.pulumi_aws_adapter import (
        _resolve_instance_type,
    )
    inst, mapped = _resolve_instance_type("g4dn.xlarge")
    assert inst == "g4dn.xlarge"
    assert mapped is None


def test_resolve_instance_type_maps_t4():
    from inferia.services.orchestration.services.adapter_engine.adapters.pulumi.pulumi_aws_adapter import (
        _resolve_instance_type,
    )
    inst, mapped = _resolve_instance_type("T4")
    assert inst == "g4dn.xlarge"
    assert mapped == "T4"


def test_resolve_instance_type_unknown_gpu_passes_through():
    """Unknown values pass through unchanged — Pulumi will surface the
    AWS error via pulumi_up/failed, which the new UX captures."""
    from inferia.services.orchestration.services.adapter_engine.adapters.pulumi.pulumi_aws_adapter import (
        _resolve_instance_type,
    )
    inst, mapped = _resolve_instance_type("MADEUP_GPU")
    assert inst == "MADEUP_GPU"
    assert mapped is None


def test_resolve_instance_type_is_case_insensitive():
    from inferia.services.orchestration.services.adapter_engine.adapters.pulumi.pulumi_aws_adapter import (
        _resolve_instance_type,
    )
    inst, mapped = _resolve_instance_type("t4")
    assert inst == "g4dn.xlarge"
    assert mapped == "t4"


@pytest.mark.asyncio
async def test_provision_node_maps_gpu_name_to_instance_type():
    """When provider_resource_id is a GPU name like 'T4', provision_node
    rewrites it to the mapped EC2 instance type and emits a prepare/log
    event documenting the substitution."""
    adapter = PulumiAWSAdapter(state_dir="/tmp/pulumi-test")
    adapter._db = AsyncMock()
    writer = RecordingWriter()
    fake_stack = MagicMock()
    fake_stack.up.return_value = MagicMock(outputs=_ok_outputs())

    with patch.object(adapter, "ensure_state_dir"), \
         patch("inferia.services.orchestration.services.adapter_engine.adapters.pulumi.pulumi_aws_adapter.load_providers_config",
               new=AsyncMock(return_value=_fake_providers_config())), \
         patch("inferia.services.orchestration.services.adapter_engine.adapters.pulumi.pulumi_aws_adapter.mint_bootstrap_token",
               new=AsyncMock(return_value=("tok", uuid4()))), \
         patch("inferia.services.orchestration.services.adapter_engine.adapters.pulumi.pulumi_aws_adapter.pulumi.automation.create_or_select_stack",
               return_value=fake_stack), \
         patch("inferia.services.orchestration.services.adapter_engine.adapters.pulumi.pulumi_aws_adapter.build_ec2_program") as bp:
        await adapter.provision_node(
            provider_resource_id="T4",
            pool_id=str(uuid4()), org_id=str(uuid4()),
            progress_writer=writer,
        )
    # prepare/log must record the mapping
    logs = [c for c in writer.calls if c[0] == "prepare" and c[1] == "log"]
    assert len(logs) == 1
    assert "T4" in (logs[0][2] or "")
    assert "g4dn.xlarge" in (logs[0][2] or "")
    # The Pulumi program builder must have received the mapped instance type
    assert bp.call_args.kwargs["instance_type"] == "g4dn.xlarge"


@pytest.mark.asyncio
async def test_provision_node_passes_through_explicit_instance_type():
    """When provider_resource_id is already an EC2 instance type
    (contains '.'), no mapping log fires."""
    adapter = PulumiAWSAdapter(state_dir="/tmp/pulumi-test")
    adapter._db = AsyncMock()
    writer = RecordingWriter()
    fake_stack = MagicMock()
    fake_stack.up.return_value = MagicMock(outputs=_ok_outputs())

    with patch.object(adapter, "ensure_state_dir"), \
         patch("inferia.services.orchestration.services.adapter_engine.adapters.pulumi.pulumi_aws_adapter.load_providers_config",
               new=AsyncMock(return_value=_fake_providers_config())), \
         patch("inferia.services.orchestration.services.adapter_engine.adapters.pulumi.pulumi_aws_adapter.mint_bootstrap_token",
               new=AsyncMock(return_value=("tok", uuid4()))), \
         patch("inferia.services.orchestration.services.adapter_engine.adapters.pulumi.pulumi_aws_adapter.pulumi.automation.create_or_select_stack",
               return_value=fake_stack), \
         patch("inferia.services.orchestration.services.adapter_engine.adapters.pulumi.pulumi_aws_adapter.build_ec2_program") as bp:
        await adapter.provision_node(
            provider_resource_id="g5.xlarge",
            pool_id=str(uuid4()), org_id=str(uuid4()),
            progress_writer=writer,
        )
    logs = [c for c in writer.calls if c[0] == "prepare" and c[1] == "log"]
    assert logs == []
    assert bp.call_args.kwargs["instance_type"] == "g5.xlarge"

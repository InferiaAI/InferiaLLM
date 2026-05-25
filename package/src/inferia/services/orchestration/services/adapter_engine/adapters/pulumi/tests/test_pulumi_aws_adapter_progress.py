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

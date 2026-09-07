"""Tests for adapter_engine.registry — confirms Pulumi is the cloud path."""
from unittest.mock import AsyncMock, patch

import pytest

from orchestration.provisioning.engine.registry import (
    get_adapter, ADAPTER_REGISTRY,
    is_direct_provision_provider,
    _deprovision_direct_node,
)
from providers.nosana.nosana_adapter import NosanaAdapter
from providers.pulumi.pulumi_aws_adapter import (
    PulumiAWSAdapter,
)
from providers.pulumi.pulumi_gcp_adapter import (
    PulumiGCPAdapter,
)
from providers.pulumi.pulumi_azure_adapter import (
    PulumiAzureAdapter,
)


def test_aws_resolves_to_pulumi_aws():
    assert "aws" in ADAPTER_REGISTRY
    a = get_adapter("aws")
    assert isinstance(a, PulumiAWSAdapter)


def test_gcp_resolves_to_pulumi_gcp():
    a = get_adapter("gcp")
    assert isinstance(a, PulumiGCPAdapter)


def test_azure_resolves_to_pulumi_azure():
    a = get_adapter("azure")
    assert isinstance(a, PulumiAzureAdapter)


def test_lambda_and_runpod_unregistered():
    for name in ("lambda", "runpod"):
        with pytest.raises(ValueError):
            get_adapter(name)


def test_skypilot_module_removed():
    with pytest.raises(ImportError):
        import providers.skypilot  # noqa: F401


def test_boto3_aws_adapter_removed():
    """The unregistered boto3 adapter from an earlier iteration is gone."""
    with pytest.raises(ImportError):
        from providers.aws import (
            aws_adapter,  # noqa: F401
        )


def test_nosana_akash_k8s_worker_still_registered():
    for name in ("nosana", "akash", "k8s", "worker", "on_prem"):
        assert name in ADAPTER_REGISTRY


def test_unknown_provider_raises_valueerror():
    with pytest.raises(ValueError):
        get_adapter("definitely-not-a-provider")


# ---------------------------------------------------------------------------
# is_direct_provision_provider — the "needs adapter.deprovision_node on delete"
# classifier. True iff the adapter supports_direct_provisioning AND is NOT a
# CLOUD adapter (cloud = Pulumi/reconciler-managed teardown).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "provider,expected",
    [
        # DePIN / direct-provision providers stop an external paid job on delete.
        ("nosana", True),
        ("akash", True),
        ("k8s", True),
        # Cloud providers tear down via the Pulumi reconciler (force_cancel),
        # NOT adapter.deprovision_node on the delete handler.
        ("aws", False),
        ("gcp", False),
        ("azure", False),
        # Worker / on-prem are not direct-provisioned (no external job to stop).
        ("worker", False),
        ("on_prem", False),
        # Unknown providers are never direct-provision.
        ("definitely-not-a-provider", False),
        ("", False),
        (None, False),
    ],
)
def test_is_direct_provision_provider(provider, expected):
    assert is_direct_provision_provider(provider) is expected


def test_is_direct_provision_covers_every_registry_key():
    """Every registered provider classifies without instantiating its adapter
    (KubernetesAdapter() would load kubeconfig and raise)."""
    for name in ADAPTER_REGISTRY:
        # Must not raise and must return a bool.
        assert isinstance(is_direct_provision_provider(name), bool)


# ---------------------------------------------------------------------------
# _deprovision_direct_node — stop the external DePIN job before marking the
# inventory row terminated. Returns True on success/skip, False on failure
# (so the caller can stamp a deprovision_failed marker).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_deprovision_direct_node_calls_adapter():
    node_row = {"provider": "nosana", "provider_instance_id": "job-abc"}
    fake = AsyncMock(spec=NosanaAdapter)
    with patch(
        "orchestration.provisioning.engine.registry.get_adapter",
        return_value=fake,
    ) as get:
        ok, err = await _deprovision_direct_node(
            node_row, pool_credential_name="cred-1",
        )
    assert ok is True
    assert err is None
    get.assert_called_once_with("nosana")
    fake.deprovision_node.assert_awaited_once_with(
        provider_instance_id="job-abc",
        provider_credential_name="cred-1",
    )


@pytest.mark.asyncio
async def test_deprovision_direct_node_skips_placeholder():
    node_row = {"provider": "nosana", "provider_instance_id": "placeholder:xyz"}
    with patch(
        "orchestration.provisioning.engine.registry.get_adapter",
    ) as get:
        ok, err = await _deprovision_direct_node(node_row, pool_credential_name="c")
    assert ok is True
    assert err is None
    get.assert_not_called()


@pytest.mark.asyncio
async def test_deprovision_direct_node_skips_missing_pii():
    for pii in (None, ""):
        node_row = {"provider": "nosana", "provider_instance_id": pii}
        with patch(
            "orchestration.provisioning.engine.registry.get_adapter",
        ) as get:
            ok, err = await _deprovision_direct_node(
                node_row, pool_credential_name="c",
            )
        assert ok is True
        assert err is None
        get.assert_not_called()


@pytest.mark.asyncio
async def test_deprovision_direct_node_returns_false_on_failure():
    node_row = {"provider": "nosana", "provider_instance_id": "job-abc"}
    fake = AsyncMock(spec=NosanaAdapter)
    fake.deprovision_node.side_effect = RuntimeError("sidecar down")
    with patch(
        "orchestration.provisioning.engine.registry.get_adapter",
        return_value=fake,
    ):
        ok, err = await _deprovision_direct_node(node_row, pool_credential_name="c")
    # Failure is surfaced (False) with the error string, NOT swallowed silently
    # — the caller stamps a deprovision_failed marker (with the reason) but
    # still marks the node terminated.
    assert ok is False
    assert err == "sidecar down"

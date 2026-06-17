"""Tests for adapter_engine.registry — confirms Pulumi is the cloud path."""
from unittest.mock import AsyncMock, patch

import pytest

from orchestration.provisioning.engine.registry import (
    get_adapter, ADAPTER_REGISTRY,
    is_direct_provision_provider,
    provider_prefers_origin_model_fetch,
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
# provider_prefers_origin_model_fetch — gates the HF-mirror bypass. True for
# public-cloud providers (direct internet egress → pull from origin), False
# for DePIN / self-hosted / air-gapped (keep the cache-first mirror).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "provider,expected",
    [
        # Public cloud → fetch from origin, bypass the CP mirror.
        ("aws", True),
        ("gcp", True),
        ("azure", True),
        # DePIN / self-hosted / k8s → keep the cache-first mirror.
        ("nosana", False),
        ("akash", False),
        ("k8s", False),
        ("worker", False),
        ("on_prem", False),
        # Unknown / unset → never bypass (safe default).
        ("definitely-not-a-provider", False),
        ("", False),
        (None, False),
    ],
)
def test_provider_prefers_origin_model_fetch(provider, expected):
    assert provider_prefers_origin_model_fetch(provider) is expected


def test_prefers_origin_covers_every_registry_key_without_instantiation():
    """Classifies every provider via CLASS attributes only (no adapter
    instantiation — k8s/cloud adapters raise at __init__)."""
    for name in ADAPTER_REGISTRY:
        assert isinstance(provider_prefers_origin_model_fetch(name), bool)


def test_prefers_origin_matches_cloud_adapter_type():
    """The bypass set is exactly the CLOUD adapters — keeps the flag and the
    ADAPTER_TYPE classification from silently diverging."""
    from orchestration.provisioning.engine.base import AdapterType
    for name, cls in ADAPTER_REGISTRY.items():
        is_cloud = getattr(cls, "ADAPTER_TYPE", None) == AdapterType.CLOUD
        assert provider_prefers_origin_model_fetch(name) is is_cloud, name


def test_capabilities_to_dict_includes_prefers_origin():
    from orchestration.provisioning.engine.base import ProviderCapabilities
    d = ProviderCapabilities(prefers_origin_model_fetch=True).to_dict()
    assert d["prefers_origin_model_fetch"] is True
    assert ProviderCapabilities().to_dict()["prefers_origin_model_fetch"] is False


def test_get_provider_info_exposes_prefers_origin():
    """The capability is surfaced through get_provider_info (the dashboard
    /inventory/providers payload), True for cloud, False for DePIN/worker."""
    from orchestration.provisioning.engine.registry import get_provider_info
    info = get_provider_info()
    assert info["aws"]["capabilities"]["prefers_origin_model_fetch"] is True
    assert info["nosana"]["capabilities"]["prefers_origin_model_fetch"] is False
    assert info["worker"]["capabilities"]["prefers_origin_model_fetch"] is False


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

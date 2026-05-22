"""Tests for adapter_engine.registry — confirms Pulumi is the cloud path."""
import pytest

from inferia.services.orchestration.services.adapter_engine.registry import (
    get_adapter, ADAPTER_REGISTRY,
)
from inferia.services.orchestration.services.adapter_engine.adapters.pulumi.pulumi_aws_adapter import (
    PulumiAWSAdapter,
)
from inferia.services.orchestration.services.adapter_engine.adapters.pulumi.pulumi_gcp_adapter import (
    PulumiGCPAdapter,
)
from inferia.services.orchestration.services.adapter_engine.adapters.pulumi.pulumi_azure_adapter import (
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
        import inferia.services.orchestration.services.adapter_engine.adapters.skypilot  # noqa: F401


def test_boto3_aws_adapter_removed():
    """The unregistered boto3 adapter from an earlier iteration is gone."""
    with pytest.raises(ImportError):
        from inferia.services.orchestration.services.adapter_engine.adapters.aws import (
            aws_adapter,  # noqa: F401
        )


def test_nosana_akash_k8s_worker_still_registered():
    for name in ("nosana", "akash", "k8s", "worker", "on_prem"):
        assert name in ADAPTER_REGISTRY


def test_unknown_provider_raises_valueerror():
    with pytest.raises(ValueError):
        get_adapter("definitely-not-a-provider")

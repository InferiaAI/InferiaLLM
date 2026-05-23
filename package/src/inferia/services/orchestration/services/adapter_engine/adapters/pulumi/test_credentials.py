"""Tests for credential resolution — ProvidersConfig → Pulumi env vars."""
import pytest

from inferia.services.api_gateway.config import (
    AWSConfig,
    AzureConfig,
    CloudConfig,
    GCPConfig,
    ProvidersConfig,
)
from inferia.services.orchestration.services.adapter_engine.adapters.pulumi.credentials import (
    MissingCredentialsError,
    resolve_aws_env,
    resolve_azure_env,
    resolve_gcp_env,
)


def _aws_cfg(**kw):
    return ProvidersConfig(cloud=CloudConfig(aws=AWSConfig(**kw)))


def test_resolve_aws_env_happy():
    cfg = _aws_cfg(
        access_key_id="AKIAREALKEY1234XYZ8",
        secret_access_key="real-secret-not-mask",
        region="us-west-2",
    )
    env = resolve_aws_env(cfg)
    assert env == {
        "AWS_ACCESS_KEY_ID": "AKIAREALKEY1234XYZ8",
        "AWS_SECRET_ACCESS_KEY": "real-secret-not-mask",
        "AWS_DEFAULT_REGION": "us-west-2",
    }


def test_resolve_aws_env_missing_key_raises():
    cfg = _aws_cfg(secret_access_key="x", region="us-east-1")
    with pytest.raises(MissingCredentialsError):
        resolve_aws_env(cfg)


def test_resolve_aws_env_missing_secret_raises():
    cfg = _aws_cfg(access_key_id="AKIA", region="us-east-1")
    with pytest.raises(MissingCredentialsError):
        resolve_aws_env(cfg)


def test_resolve_aws_env_masked_key_rejected():
    cfg = _aws_cfg(
        access_key_id="AKIA...XYZ8",  # masked
        secret_access_key="real",
        region="us-east-1",
    )
    with pytest.raises(MissingCredentialsError):
        resolve_aws_env(cfg)


def test_resolve_aws_env_masked_secret_rejected():
    cfg = _aws_cfg(
        access_key_id="AKIAREALKEY1234XYZ8",
        secret_access_key="********",  # masked
        region="us-east-1",
    )
    with pytest.raises(MissingCredentialsError):
        resolve_aws_env(cfg)


def test_resolve_aws_env_default_region_when_blank():
    cfg = _aws_cfg(
        access_key_id="AKIAREALKEY1234XYZ8",
        secret_access_key="real-secret-not-mask",
        region="",
    )
    env = resolve_aws_env(cfg)
    assert env["AWS_DEFAULT_REGION"] == "us-east-1"


def test_resolve_gcp_env_with_service_account_json(tmp_path):
    cfg = ProvidersConfig(cloud=CloudConfig(
        gcp=GCPConfig(
            project_id="my-proj",
            region="us-central1",
            service_account_json='{"type":"service_account"}',
        )
    ))
    env = resolve_gcp_env(cfg, write_dir=str(tmp_path))
    assert env["GOOGLE_PROJECT"] == "my-proj"
    assert env["GOOGLE_REGION"] == "us-central1"
    sa_path = env["GOOGLE_APPLICATION_CREDENTIALS"]
    assert sa_path.startswith(str(tmp_path))
    with open(sa_path) as f:
        assert '"type":"service_account"' in f.read()


def test_resolve_gcp_env_missing_project_raises():
    cfg = ProvidersConfig(cloud=CloudConfig(gcp=GCPConfig()))
    with pytest.raises(MissingCredentialsError):
        resolve_gcp_env(cfg, write_dir="/tmp")


def test_resolve_azure_env_with_service_principal():
    cfg = ProvidersConfig(cloud=CloudConfig(
        azure=AzureConfig(
            subscription_id="sub-1",
            tenant_id="tenant-1",
            client_id="client-1",
            client_secret="real-secret",
        )
    ))
    env = resolve_azure_env(cfg)
    assert env["ARM_SUBSCRIPTION_ID"] == "sub-1"
    assert env["ARM_TENANT_ID"] == "tenant-1"
    assert env["ARM_CLIENT_ID"] == "client-1"
    assert env["ARM_CLIENT_SECRET"] == "real-secret"


def test_resolve_azure_env_missing_secret_raises():
    cfg = ProvidersConfig(cloud=CloudConfig(
        azure=AzureConfig(
            subscription_id="sub-1",
            tenant_id="tenant-1",
            client_id="client-1",
        )
    ))
    with pytest.raises(MissingCredentialsError):
        resolve_azure_env(cfg)

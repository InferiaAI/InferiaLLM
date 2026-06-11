"""Tests for credential resolution — ProvidersConfig → Pulumi env vars."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from api_gateway.config import (
    AWSConfig,
    AzureConfig,
    CloudConfig,
    GCPConfig,
    ProvidersConfig,
)
from providers.pulumi.credentials import (
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
    )
    env = resolve_aws_env(cfg)
    assert env == {
        "AWS_ACCESS_KEY_ID": "AKIAREALKEY1234XYZ8",
        "AWS_SECRET_ACCESS_KEY": "real-secret-not-mask",
        "AWS_DEFAULT_REGION": "us-east-1",
    }


def test_resolve_aws_env_missing_key_raises():
    cfg = _aws_cfg(secret_access_key="x")
    with pytest.raises(MissingCredentialsError):
        resolve_aws_env(cfg)


def test_resolve_aws_env_missing_secret_raises():
    cfg = _aws_cfg(access_key_id="AKIA")
    with pytest.raises(MissingCredentialsError):
        resolve_aws_env(cfg)


def test_resolve_aws_env_masked_key_rejected():
    cfg = _aws_cfg(
        access_key_id="AKIA...XYZ8",  # masked
        secret_access_key="real",
    )
    with pytest.raises(MissingCredentialsError):
        resolve_aws_env(cfg)


def test_resolve_aws_env_masked_secret_rejected():
    cfg = _aws_cfg(
        access_key_id="AKIAREALKEY1234XYZ8",
        secret_access_key="********",  # masked
    )
    with pytest.raises(MissingCredentialsError):
        resolve_aws_env(cfg)


def test_resolve_aws_env_region_always_us_east_1():
    """AWS_DEFAULT_REGION is always us-east-1; region was removed from AWSConfig."""
    cfg = _aws_cfg(
        access_key_id="AKIAREALKEY1234XYZ8",
        secret_access_key="real-secret-not-mask",
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


# --- verify_credentials tests --------------------------------------------
# Preflight check that hits sts:GetCallerIdentity.

from providers.pulumi.credentials import (
    AWSCredentials,
    verify_credentials,
)
from orchestration.provisioning_state_machine.errors import (
    InvalidCredentialsError, NetworkError,
)


def _creds(**over) -> AWSCredentials:
    base = dict(
        access_key_id="AKIA...",
        secret_access_key="secret",
        region="us-east-1",
        session_token=None,
    )
    base.update(over)
    return AWSCredentials(**base)


def test_verify_credentials_returns_caller_identity_on_success():
    fake_client = MagicMock()
    fake_client.get_caller_identity.return_value = {
        "UserId": "AIDA...", "Account": "123456789012",
        "Arn": "arn:aws:iam::123:user/test",
    }
    with patch(
        "orchestration.adapter_engine."
        "adapters.pulumi.credentials._boto3_sts_client",
        return_value=fake_client,
    ):
        ident = verify_credentials(_creds())
    assert ident["Account"] == "123456789012"


def test_verify_credentials_raises_invalid_credentials_on_authfailure():
    from botocore.exceptions import ClientError
    err = ClientError(
        error_response={"Error": {
            "Code": "InvalidClientTokenId",
            "Message": "The security token included in the request is invalid.",
        }},
        operation_name="GetCallerIdentity",
    )
    fake_client = MagicMock()
    fake_client.get_caller_identity.side_effect = err
    with patch(
        "orchestration.adapter_engine."
        "adapters.pulumi.credentials._boto3_sts_client",
        return_value=fake_client,
    ):
        with pytest.raises(InvalidCredentialsError):
            verify_credentials(_creds())


def test_verify_credentials_raises_network_error_on_endpoint_failure():
    from botocore.exceptions import EndpointConnectionError
    err = EndpointConnectionError(endpoint_url="https://sts.us-east-1.amazonaws.com/")
    fake_client = MagicMock()
    fake_client.get_caller_identity.side_effect = err
    with patch(
        "orchestration.adapter_engine."
        "adapters.pulumi.credentials._boto3_sts_client",
        return_value=fake_client,
    ):
        with pytest.raises(NetworkError):
            verify_credentials(_creds())

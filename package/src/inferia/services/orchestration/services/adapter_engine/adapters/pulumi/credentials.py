"""Resolve provider credentials from ProvidersConfig into env-var dicts
that Pulumi can inherit. Each function raises MissingCredentialsError
when required fields are absent or look masked (defensive — the gateway
already prevents masked round-trips, but defend at the adapter boundary
too).
"""
from __future__ import annotations

import os
import tempfile

from inferia.services.api_gateway.config import ProvidersConfig
from inferia.services.api_gateway.management.configuration import _is_masked


class MissingCredentialsError(ValueError):
    """Raised when ProvidersConfig is missing required credentials for a
    cloud adapter, or when supplied credentials look like masked values
    accidentally round-tripped through the dashboard."""


def _require(value: str | None, field_name: str) -> str:
    if not value:
        raise MissingCredentialsError(f"{field_name} is required")
    if _is_masked(value):
        raise MissingCredentialsError(
            f"{field_name} looks masked — re-enter the real value"
        )
    return value


def resolve_aws_env(cfg: ProvidersConfig) -> dict[str, str]:
    """Return env vars Pulumi-AWS will inherit. AWS_DEFAULT_REGION
    falls back to us-east-1 when the config has no region."""
    aws = cfg.cloud.aws
    key = _require(aws.access_key_id, "access_key_id")
    secret = _require(aws.secret_access_key, "secret_access_key")
    region = aws.region or "us-east-1"
    return {
        "AWS_ACCESS_KEY_ID": key,
        "AWS_SECRET_ACCESS_KEY": secret,
        "AWS_DEFAULT_REGION": region,
    }


def resolve_gcp_env(cfg: ProvidersConfig, *, write_dir: str | None = None) -> dict[str, str]:
    """Return env vars Pulumi-GCP will inherit. The service-account JSON
    is written to a tempfile under write_dir (defaults to tempfile.gettempdir())
    and GOOGLE_APPLICATION_CREDENTIALS points at it."""
    gcp = cfg.cloud.gcp
    project = _require(gcp.project_id, "project_id")
    region = gcp.region or "us-central1"
    env = {"GOOGLE_PROJECT": project, "GOOGLE_REGION": region}
    if gcp.service_account_json:
        if _is_masked(gcp.service_account_json):
            raise MissingCredentialsError("service_account_json looks masked")
        d = write_dir or tempfile.gettempdir()
        os.makedirs(d, exist_ok=True)
        fd, path = tempfile.mkstemp(prefix="gcp-sa-", suffix=".json", dir=d)
        with os.fdopen(fd, "w") as f:
            f.write(gcp.service_account_json)
        os.chmod(path, 0o600)
        env["GOOGLE_APPLICATION_CREDENTIALS"] = path
    return env


def resolve_azure_env(cfg: ProvidersConfig) -> dict[str, str]:
    """Return env vars Pulumi-Azure-Native will inherit (ARM_* form for
    service-principal auth)."""
    az = cfg.cloud.azure
    sub = _require(az.subscription_id, "subscription_id")
    tenant = _require(az.tenant_id, "tenant_id")
    client = _require(az.client_id, "client_id")
    secret = _require(az.client_secret, "client_secret")
    return {
        "ARM_SUBSCRIPTION_ID": sub,
        "ARM_TENANT_ID": tenant,
        "ARM_CLIENT_ID": client,
        "ARM_CLIENT_SECRET": secret,
    }

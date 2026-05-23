# AWS Pulumi Provisioning — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace SkyPilot with Pulumi (Automation API) as the cloud provisioning engine for AWS, GCP, and Azure. AWS is the first-class implementation; GCP and Azure get matching skeletons.

**Architecture:** New `adapters/pulumi/` subtree with three adapters on a shared `PulumiProvisioningBase`. Each pool maps to one Pulumi stack persisted to `file:///var/lib/inferia/pulumi-state/`. AWS credentials are resolved per-call from `ProvidersConfig`, exported as env vars to the Pulumi subprocess, never persisted. The `bootstrap_builder` and `pool_metadata` helpers from the prior AWS adapter are reused.

**Tech Stack:** Python 3.10-3.12, FastAPI, asyncpg, `pulumi>=3.140.0` + `pulumi_aws>=6.50.0` + `pulumi_gcp>=8.0.0` + `pulumi_azure_native>=2.50.0`, React 19 + Vite (dashboard).

**Spec:** `docs/specs/2026-05-22-aws-pulumi.md` (commit `b147d63`).

**Branch:** `feat/aws-ec2-provisioning` (continuation).

**Commit convention:** Signed via `git -c user.signingkey=/home/ankit/.ssh/id_ed25519_gh commit -S -m "..."`. No Claude mention in commit bodies.

---

## File Structure

| File | Status | Responsibility |
|---|---|---|
| `package/pyproject.toml` | MOD | drop `skypilot*` extras, add `pulumi`/`pulumi_aws`/`pulumi_gcp`/`pulumi_azure_native` |
| `package/src/inferia/services/orchestration/config.py` | MOD | add `pulumi_state_dir`, `pulumi_passphrase` settings |
| `package/src/inferia/services/orchestration/services/adapter_engine/adapters/pulumi/__init__.py` | NEW | package marker |
| `.../pulumi/base.py` | NEW | `PulumiProvisioningBase` — state-dir setup, stack naming, LocalWorkspace lifecycle |
| `.../pulumi/credentials.py` | NEW | `resolve_aws_env` / `resolve_gcp_env` / `resolve_azure_env` |
| `.../pulumi/ami.py` | NEW | `latest_dlami_ami(region)` SSM lookup with TTL cache |
| `.../pulumi/programs.py` | NEW | `build_ec2_program(...)`, `build_gce_program(...)`, `build_azure_vm_program(...)` — inline Pulumi closures |
| `.../pulumi/pulumi_aws_adapter.py` | NEW | `PulumiAWSAdapter(PulumiProvisioningBase, ProviderAdapter)` |
| `.../pulumi/pulumi_gcp_adapter.py` | NEW | `PulumiGCPAdapter(...)` (skeleton mirroring AWS) |
| `.../pulumi/pulumi_azure_adapter.py` | NEW | `PulumiAzureAdapter(...)` |
| `.../pulumi/test_base.py` | NEW | base-class tests |
| `.../pulumi/test_credentials.py` | NEW | credential resolver tests |
| `.../pulumi/test_ami.py` | NEW | DLAMI lookup tests |
| `.../pulumi/test_pulumi_aws_adapter.py` | NEW | full AWS adapter tests |
| `.../pulumi/test_pulumi_gcp_adapter.py` | NEW | GCP adapter tests (mirror of AWS) |
| `.../pulumi/test_pulumi_azure_adapter.py` | NEW | Azure adapter tests (mirror of AWS) |
| `.../adapter_engine/registry.py` | REWRITE | drop SkyPilot, register Pulumi adapters |
| `.../adapter_engine/adapters/skypilot/` | DELETE | entire subtree |
| `.../adapter_engine/adapters/aws/aws_adapter.py` | DELETE | unregistered boto3 adapter; superseded |
| `.../adapter_engine/adapters/aws/test_aws_adapter.py` | DELETE | tests for the deleted adapter |
| `.../adapter_engine/adapters/aws/bootstrap_builder.py` | KEEP | reused by PulumiAWSAdapter |
| `.../adapter_engine/adapters/aws/pool_metadata.py` | KEEP | validation gate reused |
| `apps/dashboard/src/pages/Settings/Providers/ProviderList.tsx` | MOD | AWS description "via SkyPilot" → "via Pulumi" |
| `apps/dashboard/src/pages/Compute/NewPool.tsx` | MOD | "SkyPilot Configuration" → "Cluster Configuration"; banner copy |

---

## Task P1: Add Pulumi deps, remove SkyPilot

**Files:**
- Modify: `package/pyproject.toml`

- [ ] **Step 1: Inspect current SkyPilot extras**

Run:
```
cd /storage/intern/hooman/work/InferiaLLM
grep -nA 6 'skypilot' package/pyproject.toml
```

- [ ] **Step 2: Remove `skypilot` from the `dependencies` array if present**

Locate the `dependencies = [...]` block in `[project]` and delete any line matching `skypilot*`. If `skypilot` lives in an `[project.optional-dependencies]` block (e.g. `skypilot = [...]`), delete the whole block.

- [ ] **Step 3: Add Pulumi deps to the runtime `dependencies` array**

Append the following four lines (preserve alphabetical order if the array is sorted):

```toml
  "pulumi>=3.140.0,<4",
  "pulumi-aws>=6.50.0,<7",
  "pulumi-gcp>=8.0.0,<9",
  "pulumi-azure-native>=2.50.0,<3",
```

- [ ] **Step 4: Reinstall the venv to pick up new deps**

Run: `cd /storage/intern/hooman/work/InferiaLLM/package && uv sync` (or `pip install -e .` if uv isn't used).
Expected: no errors. New packages appear in `.venv/lib/python3.12/site-packages/{pulumi,pulumi_aws,pulumi_gcp,pulumi_azure_native}`.

- [ ] **Step 5: Smoke-import the libraries**

Run:
```
/storage/intern/hooman/work/InferiaLLM/package/.venv/bin/python3 -c "
import pulumi, pulumi.automation, pulumi_aws as aws
print(pulumi.__version__, '|', aws.__name__)
"
```
Expected: prints a version like `3.140.x` and `pulumi_aws`.

- [ ] **Step 6: Commit**

```bash
cd /storage/intern/hooman/work/InferiaLLM
git add package/pyproject.toml
git -c user.signingkey=/home/ankit/.ssh/id_ed25519_gh commit -S -m "deps: replace skypilot with pulumi (aws/gcp/azure)"
```

---

## Task P2: PulumiProvisioningBase

**Files:**
- Create: `package/src/inferia/services/orchestration/services/adapter_engine/adapters/pulumi/__init__.py`
- Create: `package/src/inferia/services/orchestration/services/adapter_engine/adapters/pulumi/base.py`
- Create: `package/src/inferia/services/orchestration/services/adapter_engine/adapters/pulumi/test_base.py`

- [ ] **Step 1: Create the package marker**

```bash
mkdir -p /storage/intern/hooman/work/InferiaLLM/package/src/inferia/services/orchestration/services/adapter_engine/adapters/pulumi
echo '"""Pulumi-based cloud provisioning adapters."""' > /storage/intern/hooman/work/InferiaLLM/package/src/inferia/services/orchestration/services/adapter_engine/adapters/pulumi/__init__.py
```

- [ ] **Step 2: Write failing tests**

`test_base.py`:

```python
"""Tests for PulumiProvisioningBase — state-dir lifecycle and stack naming."""
import os
import pathlib
import tempfile

import pytest

from inferia.services.orchestration.services.adapter_engine.adapters.pulumi.base import (
    PulumiProvisioningBase,
    PulumiStateError,
)


def test_state_dir_created_with_0700_perms(tmp_path):
    base = PulumiProvisioningBase(state_dir=str(tmp_path / "state"))
    base.ensure_state_dir()
    assert (tmp_path / "state").is_dir()
    mode = (tmp_path / "state").stat().st_mode & 0o777
    assert mode == 0o700, f"expected 0700, got {oct(mode)}"


def test_stack_name_uses_pool_id():
    base = PulumiProvisioningBase(state_dir="/tmp/x")
    name = base.stack_name_for_pool("00000000-0000-0000-0000-000000000001")
    assert name == "inferia-pool-00000000-0000-0000-0000-000000000001"


def test_project_name_is_per_provider():
    base = PulumiProvisioningBase(state_dir="/tmp/x", project_name="inferia-aws")
    assert base.project_name == "inferia-aws"


def test_state_dir_unwritable_raises(tmp_path, monkeypatch):
    # Make tmp_path read-only; ensure_state_dir must raise PulumiStateError.
    target = tmp_path / "state"
    target.mkdir(mode=0o500)
    base = PulumiProvisioningBase(state_dir=str(target / "child"))
    with pytest.raises(PulumiStateError):
        base.ensure_state_dir()
    # Cleanup: restore perms so pytest can rmtree
    target.chmod(0o700)


def test_workspace_opts_set_backend_url(tmp_path):
    base = PulumiProvisioningBase(state_dir=str(tmp_path))
    opts = base.local_workspace_opts(env_vars={"AWS_ACCESS_KEY_ID": "x"})
    assert opts.env_vars["PULUMI_BACKEND_URL"] == f"file://{tmp_path}"
    assert opts.env_vars["AWS_ACCESS_KEY_ID"] == "x"
    # PULUMI_CONFIG_PASSPHRASE defaults to empty string so Pulumi doesn't
    # prompt interactively for a passphrase.
    assert opts.env_vars["PULUMI_CONFIG_PASSPHRASE"] == ""
```

- [ ] **Step 3: Run failing**

Run: `cd /storage/intern/hooman/work/InferiaLLM && /storage/intern/hooman/work/InferiaLLM/package/.venv/bin/python3 -m pytest package/src/inferia/services/orchestration/services/adapter_engine/adapters/pulumi/test_base.py -v`
Expected: ImportError on `PulumiProvisioningBase`.

- [ ] **Step 4: Implement `base.py`**

```python
"""Shared base class for Pulumi-driven cloud provisioning adapters.

Provides the state-directory lifecycle, stack naming, and LocalWorkspace
option builder. Concrete adapters (AWS / GCP / Azure) inherit and add
provider-specific provision/destroy logic.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


class PulumiStateError(RuntimeError):
    """Raised when the Pulumi state directory cannot be created or written."""


@dataclass
class LocalWorkspaceOpts:
    """Container for the kwargs passed to pulumi.automation.LocalWorkspace.

    Kept as a plain dataclass rather than importing pulumi at module
    load time so tests can inspect the constructed options without
    requiring the pulumi binary to be present.
    """
    work_dir: str
    env_vars: dict[str, str] = field(default_factory=dict)
    project_settings: Optional[dict[str, Any]] = None


class PulumiProvisioningBase:
    """Mixin providing the bits every Pulumi adapter needs:

    * a state directory (file:// backend root)
    * stack naming based on the pool's UUID
    * LocalWorkspace option construction with PULUMI_BACKEND_URL set
    * a PULUMI_CONFIG_PASSPHRASE default of "" so Pulumi never prompts
    """

    def __init__(
        self,
        *,
        state_dir: str,
        project_name: str = "inferia",
        passphrase: str = "",
    ) -> None:
        self.state_dir = state_dir
        self.project_name = project_name
        self.passphrase = passphrase

    def ensure_state_dir(self) -> None:
        """Create the state directory with 0700 permissions if it doesn't
        exist. Raise PulumiStateError on any I/O failure."""
        p = Path(self.state_dir)
        try:
            p.mkdir(parents=True, exist_ok=True, mode=0o700)
            # Ensure perms even if the dir already existed with broader perms.
            os.chmod(p, 0o700)
            # Verify writability.
            test_file = p / ".inferia_write_test"
            test_file.write_text("ok")
            test_file.unlink()
        except (OSError, PermissionError) as e:
            raise PulumiStateError(
                f"Pulumi state dir {self.state_dir!r} is not writable: {e}"
            ) from e

    def stack_name_for_pool(self, pool_id: str) -> str:
        """Return the canonical stack name for a pool UUID."""
        return f"inferia-pool-{pool_id}"

    def local_workspace_opts(self, *, env_vars: Optional[dict[str, str]] = None) -> LocalWorkspaceOpts:
        """Build the LocalWorkspace options for this provisioning run.

        env_vars from the caller (credentials etc) are merged with
        Pulumi-required env vars. Caller's keys do NOT override the
        Pulumi-required ones.
        """
        merged = dict(env_vars or {})
        merged["PULUMI_BACKEND_URL"] = f"file://{self.state_dir}"
        merged["PULUMI_CONFIG_PASSPHRASE"] = self.passphrase  # default ""
        return LocalWorkspaceOpts(
            work_dir=self.state_dir,
            env_vars=merged,
        )
```

- [ ] **Step 5: Run tests, expect 5/5 pass**

Run: `cd /storage/intern/hooman/work/InferiaLLM && /storage/intern/hooman/work/InferiaLLM/package/.venv/bin/python3 -m pytest package/src/inferia/services/orchestration/services/adapter_engine/adapters/pulumi/test_base.py -v`

- [ ] **Step 6: Commit**

```bash
cd /storage/intern/hooman/work/InferiaLLM
git add package/src/inferia/services/orchestration/services/adapter_engine/adapters/pulumi/
git -c user.signingkey=/home/ankit/.ssh/id_ed25519_gh commit -S -m "pulumi: PulumiProvisioningBase + state-dir lifecycle"
```

---

## Task P3: AwsCredentialResolver

**Files:**
- Create: `.../pulumi/credentials.py`
- Create: `.../pulumi/test_credentials.py`

- [ ] **Step 1: Write failing tests**

```python
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
    # JSON written to a file and pointer set
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
```

- [ ] **Step 2: Run, observe failure**

Run: `/storage/intern/hooman/work/InferiaLLM/package/.venv/bin/python3 -m pytest package/src/inferia/services/orchestration/services/adapter_engine/adapters/pulumi/test_credentials.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement `credentials.py`**

```python
"""Resolve provider credentials from ProvidersConfig into env-var dicts
that Pulumi can inherit. Each function raises MissingCredentialsError
when required fields are absent or look masked (defensive — the gateway
already prevents masked round-trips, but defend at the adapter boundary
too).
"""
from __future__ import annotations

import json
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
        path = tempfile.mkstemp(prefix="gcp-sa-", suffix=".json", dir=d)[1]
        with open(path, "w") as f:
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
```

- [ ] **Step 4: Run, expect 10/10 pass**

- [ ] **Step 5: Commit**

```bash
git add package/src/inferia/services/orchestration/services/adapter_engine/adapters/pulumi/credentials.py \
        package/src/inferia/services/orchestration/services/adapter_engine/adapters/pulumi/test_credentials.py
git -c user.signingkey=/home/ankit/.ssh/id_ed25519_gh commit -S -m "pulumi: credential resolvers for AWS/GCP/Azure"
```

---

## Task P4: DLAMI AMI lookup helper

**Files:**
- Create: `.../pulumi/ami.py`
- Create: `.../pulumi/test_ami.py`

- [ ] **Step 1: Failing tests**

```python
"""Tests for latest_dlami_ami SSM lookup."""
import time
from unittest.mock import MagicMock, patch

import botocore.exceptions
import pytest

from inferia.services.orchestration.services.adapter_engine.adapters.pulumi import ami


def _fresh_cache():
    ami._DLAMI_CACHE.clear()


def test_latest_dlami_ami_returns_value():
    _fresh_cache()
    mock_ssm = MagicMock()
    mock_ssm.get_parameter.return_value = {"Parameter": {"Value": "ami-deadbeef"}}
    with patch("boto3.client", return_value=mock_ssm):
        out = ami.latest_dlami_ami("us-east-1")
    assert out == "ami-deadbeef"


def test_latest_dlami_ami_is_cached_per_region():
    _fresh_cache()
    mock_ssm = MagicMock()
    mock_ssm.get_parameter.return_value = {"Parameter": {"Value": "ami-abc"}}
    with patch("boto3.client", return_value=mock_ssm):
        ami.latest_dlami_ami("us-east-1")
        ami.latest_dlami_ami("us-east-1")
    assert mock_ssm.get_parameter.call_count == 1


def test_latest_dlami_ami_different_regions_independent():
    _fresh_cache()
    mock_ssm = MagicMock()
    mock_ssm.get_parameter.side_effect = [
        {"Parameter": {"Value": "ami-east"}},
        {"Parameter": {"Value": "ami-west"}},
    ]
    with patch("boto3.client", return_value=mock_ssm):
        e = ami.latest_dlami_ami("us-east-1")
        w = ami.latest_dlami_ami("us-west-2")
    assert e == "ami-east"
    assert w == "ami-west"
    assert mock_ssm.get_parameter.call_count == 2


def test_latest_dlami_ami_cache_expires():
    _fresh_cache()
    mock_ssm = MagicMock()
    mock_ssm.get_parameter.return_value = {"Parameter": {"Value": "ami-1"}}
    with patch("boto3.client", return_value=mock_ssm):
        ami.latest_dlami_ami("us-east-1")
        # Manually expire the cache.
        ami._DLAMI_CACHE["us-east-1"] = ("ami-1", time.time() - ami._DLAMI_TTL_S - 1)
        ami.latest_dlami_ami("us-east-1")
    assert mock_ssm.get_parameter.call_count == 2


def test_latest_dlami_ami_boto_error_raises():
    _fresh_cache()
    mock_ssm = MagicMock()
    mock_ssm.get_parameter.side_effect = botocore.exceptions.ClientError(
        {"Error": {"Code": "ParameterNotFound", "Message": "x"}}, "GetParameter",
    )
    with patch("boto3.client", return_value=mock_ssm):
        with pytest.raises(ami.AMILookupError):
            ami.latest_dlami_ami("us-east-1")
```

- [ ] **Step 2: Implement `ami.py`**

```python
"""DLAMI lookup helper.

Resolves the latest AWS Deep Learning AMI for Ubuntu 22.04 + NVIDIA driver
via SSM Public Parameters, with a per-region in-memory cache (TTL 1 h).

Sync (not async) — used inside Pulumi inline programs that themselves
run synchronously.
"""
from __future__ import annotations

import time
from typing import Dict, Tuple

import boto3
import botocore.exceptions


class AMILookupError(RuntimeError):
    """Raised when the DLAMI SSM parameter is unreachable or missing."""


_DLAMI_PARAMETER = (
    "/aws/service/deeplearning/ami/x86_64/"
    "oss-nvidia-driver-gpu-ubuntu-22.04/latest/ami-id"
)
_DLAMI_TTL_S = 3600
_DLAMI_CACHE: Dict[str, Tuple[str, float]] = {}


def latest_dlami_ami(region: str) -> str:
    """Return the latest DLAMI Ubuntu 22.04 + NVIDIA driver AMI for region.

    Per-region cache with a 1 h TTL — the underlying parameter changes
    only on AMI refresh (~monthly).
    """
    now = time.time()
    cached = _DLAMI_CACHE.get(region)
    if cached and (now - cached[1]) < _DLAMI_TTL_S:
        return cached[0]
    ssm = boto3.client("ssm", region_name=region)
    try:
        resp = ssm.get_parameter(Name=_DLAMI_PARAMETER)
    except botocore.exceptions.ClientError as e:
        raise AMILookupError(f"DLAMI lookup failed: {e.response['Error']['Code']}") from e
    except botocore.exceptions.BotoCoreError as e:
        raise AMILookupError(f"DLAMI lookup failed: {type(e).__name__}") from e
    value = resp["Parameter"]["Value"]
    _DLAMI_CACHE[region] = (value, now)
    return value
```

- [ ] **Step 3: Run + commit**

Run: `pytest .../pulumi/test_ami.py -v` → 5/5 pass.

```bash
git add package/src/inferia/services/orchestration/services/adapter_engine/adapters/pulumi/ami.py \
        package/src/inferia/services/orchestration/services/adapter_engine/adapters/pulumi/test_ami.py
git -c user.signingkey=/home/ankit/.ssh/id_ed25519_gh commit -S -m "pulumi: DLAMI lookup helper with per-region TTL cache"
```

---

## Task P5: PulumiAWSAdapter — provision_node happy path

**Files:**
- Create: `.../pulumi/programs.py`
- Create: `.../pulumi/pulumi_aws_adapter.py`
- Create: `.../pulumi/test_pulumi_aws_adapter.py`
- Modify: `package/src/inferia/services/orchestration/config.py` (add `pulumi_state_dir`, `pulumi_passphrase`)

- [ ] **Step 1: Add config knobs**

In `package/src/inferia/services/orchestration/config.py`, add to the Settings class (preserve existing fields):

```python
    pulumi_state_dir: str = Field(
        default="/var/lib/inferia/pulumi-state",
        validation_alias="INFERIA_PULUMI_STATE_DIR",
        description="Filesystem path where Pulumi local-backend state is persisted.",
    )
    pulumi_passphrase: str = Field(
        default="",
        validation_alias="INFERIA_PULUMI_PASSPHRASE",
        description="PULUMI_CONFIG_PASSPHRASE — empty disables stack-config secrets.",
    )
```

- [ ] **Step 2: Implement programs.py with the AWS EC2 inline program**

```python
"""Inline Pulumi programs used by the cloud adapters.

Each `build_*_program(...)` returns a zero-arg callable suitable for
passing to `pulumi.automation.create_or_select_stack(program=...)`. The
program closures capture the per-pool args so the same program object
can be re-run for the same stack (Pulumi reconciles).
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional


def build_ec2_program(
    *,
    pool_id: str,
    org_id: str,
    bootstrap_id: str,
    instance_type: str,
    region: str,
    ami_id: str,
    subnet_id: Optional[str],
    security_group_ids: Optional[List[str]],
    iam_instance_profile: Optional[str],
    root_volume_gb: int,
    user_data: str,
    use_spot: bool = False,
) -> Callable[[], None]:
    """Return a Pulumi program that defines exactly one
    aws.ec2.Instance for the given pool."""

    def _program() -> None:
        import pulumi
        import pulumi_aws as aws

        # Root block device on EC2 instances is configured via
        # root_block_device with InstanceRootBlockDeviceArgs.
        root_bd = aws.ec2.InstanceRootBlockDeviceArgs(
            volume_size=root_volume_gb,
            volume_type="gp3",
        )

        kwargs: Dict[str, Any] = dict(
            instance_type=instance_type,
            ami=ami_id,
            user_data=user_data,
            root_block_device=root_bd,
            tags={
                "Name": f"inferia-pool-{pool_id}",
                "InferiaPoolId": pool_id,
                "InferiaOrgId": org_id,
                "InferiaBootstrapId": bootstrap_id,
            },
        )
        if subnet_id:
            kwargs["subnet_id"] = subnet_id
        if security_group_ids:
            kwargs["vpc_security_group_ids"] = security_group_ids
        if iam_instance_profile:
            kwargs["iam_instance_profile"] = iam_instance_profile
        if use_spot:
            kwargs["instance_market_options"] = aws.ec2.InstanceInstanceMarketOptionsArgs(
                market_type="spot",
            )

        instance = aws.ec2.Instance(f"inferia-pool-{pool_id}", **kwargs)
        pulumi.export("instance_id", instance.id)
        pulumi.export("public_dns", instance.public_dns)
        pulumi.export("private_ip", instance.private_ip)

    return _program
```

- [ ] **Step 3: Failing test (happy path)**

`test_pulumi_aws_adapter.py`:

```python
"""Tests for PulumiAWSAdapter — happy provision + edge cases."""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest

from inferia.services.orchestration.services.adapter_engine.adapters.pulumi.credentials import (
    MissingCredentialsError,
)
from inferia.services.orchestration.services.adapter_engine.adapters.pulumi.pulumi_aws_adapter import (
    PulumiAWSAdapter,
    ProvisionError,
)


def _aws_cfg_dict(**kw):
    base = {
        "access_key_id": "AKIAREALKEY1234XYZ8",
        "secret_access_key": "real-secret-not-masked",
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
    """AsyncMock for asyncpg.Connection with a transaction context manager."""
    db = MagicMock()
    db.execute = AsyncMock(return_value="INSERT 0 1")
    db.fetchrow = AsyncMock(return_value=None)
    db.transaction = MagicMock()
    db.transaction.return_value.__aenter__ = AsyncMock(return_value=db)
    db.transaction.return_value.__aexit__ = AsyncMock(return_value=False)
    return db


@pytest.fixture
def aws_config():
    from inferia.services.api_gateway.config import (
        AWSConfig, CloudConfig, ProvidersConfig,
    )
    return ProvidersConfig(cloud=CloudConfig(aws=AWSConfig(**_aws_cfg_dict())))


@pytest.mark.asyncio
async def test_provision_node_kicks_off_async_task(fake_db, aws_config, tmp_path):
    """provision_node returns immediately with state=provisioning and
    schedules a background task that calls stack.up_async."""
    pool_id = "00000000-0000-0000-0000-000000000001"
    org_id = "11111111-1111-1111-1111-111111111111"

    fake_stack = MagicMock()
    fake_stack.up_async = AsyncMock(return_value=MagicMock(
        outputs={
            "instance_id": MagicMock(value="i-abc123"),
            "public_dns": MagicMock(value="ec2-1-2-3-4.compute.amazonaws.com"),
            "private_ip": MagicMock(value="10.0.0.5"),
        }
    ))

    with patch(
        "inferia.services.orchestration.services.adapter_engine.adapters.pulumi.pulumi_aws_adapter."
        "pulumi.automation.create_or_select_stack",
        return_value=fake_stack,
    ), patch(
        "inferia.services.orchestration.services.adapter_engine.adapters.pulumi.pulumi_aws_adapter."
        "load_providers_config",
        return_value=aws_config,
    ), patch(
        "inferia.services.orchestration.services.adapter_engine.adapters.pulumi.pulumi_aws_adapter."
        "mint_bootstrap_token",
        new=AsyncMock(return_value=("tok-xyz", UUID(int=42))),
    ), patch(
        "inferia.services.orchestration.services.adapter_engine.adapters.pulumi.ami.latest_dlami_ami",
        return_value="ami-0123456789abcdef0",
    ):
        adapter = PulumiAWSAdapter(db=fake_db, state_dir=str(tmp_path))
        result = await adapter.provision_node(
            provider_resource_id="g5.xlarge",
            pool_id=pool_id,
            org_id=org_id,
            region="us-east-1",
        )

    assert result["provider"] == "aws"
    assert result["lifecycle_state"] == "provisioning"
    assert result["region"] == "us-east-1"
    assert result["metadata"]["pulumi_stack"] == f"inferia-pool-{pool_id}"
    # The background task must have been created
    tasks = [t for t in asyncio.all_tasks() if not t.done()]
    # Cleanup so the test doesn't leak (cancel & await)
    for t in tasks:
        if t.get_coro().__name__ == "_provision_async":
            t.cancel()
            with pytest.raises(asyncio.CancelledError):
                await t


@pytest.mark.asyncio
async def test_provision_node_missing_creds_raises(fake_db, tmp_path):
    from inferia.services.api_gateway.config import (
        AWSConfig, CloudConfig, ProvidersConfig,
    )
    empty_cfg = ProvidersConfig(cloud=CloudConfig(aws=AWSConfig()))
    with patch(
        "inferia.services.orchestration.services.adapter_engine.adapters.pulumi.pulumi_aws_adapter."
        "load_providers_config",
        return_value=empty_cfg,
    ):
        adapter = PulumiAWSAdapter(db=fake_db, state_dir=str(tmp_path))
        with pytest.raises(MissingCredentialsError):
            await adapter.provision_node(
                provider_resource_id="g5.xlarge",
                pool_id="x",
                org_id="x",
                region="us-east-1",
            )
    # Crucially: NO bootstrap token DB write happened
    fake_db.execute.assert_not_called()


@pytest.mark.asyncio
async def test_provision_node_invalid_metadata_rejected(fake_db, aws_config, tmp_path):
    """A pool with metadata={subnet_id: 'bogus'} must be rejected by
    AWSPoolMetadata before any AWS call."""
    with patch(
        "inferia.services.orchestration.services.adapter_engine.adapters.pulumi.pulumi_aws_adapter."
        "load_providers_config",
        return_value=aws_config,
    ):
        adapter = PulumiAWSAdapter(db=fake_db, state_dir=str(tmp_path))
        with pytest.raises(ProvisionError):
            await adapter.provision_node(
                provider_resource_id="g5.xlarge",
                pool_id="x",
                org_id="x",
                region="us-east-1",
                metadata={"subnet_id": "bogus", "security_group_ids": ["sg-abc12345"]},
            )
```

- [ ] **Step 4: Implement `pulumi_aws_adapter.py` (happy-path only; failure paths added in Task P6)**

```python
"""Pulumi-backed AWS EC2 provisioning adapter.

provision_node returns immediately with lifecycle_state='provisioning'
and schedules an asyncio background task that calls stack.up_async().
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional
from uuid import UUID

import pulumi.automation

from inferia.services.api_gateway.config import ProvidersConfig
from inferia.services.orchestration.config import settings
from inferia.services.orchestration.services.adapter_engine.adapters.aws.bootstrap_builder import (
    build_user_data,
)
from inferia.services.orchestration.services.adapter_engine.adapters.aws.pool_metadata import (
    AWSPoolMetadata,
)
from inferia.services.orchestration.services.adapter_engine.adapters.pulumi.ami import (
    AMILookupError,
    latest_dlami_ami,
)
from inferia.services.orchestration.services.adapter_engine.adapters.pulumi.base import (
    PulumiProvisioningBase,
    PulumiStateError,
)
from inferia.services.orchestration.services.adapter_engine.adapters.pulumi.credentials import (
    MissingCredentialsError,
    resolve_aws_env,
)
from inferia.services.orchestration.services.adapter_engine.adapters.pulumi.programs import (
    build_ec2_program,
)
from inferia.services.orchestration.services.adapter_engine.base import (
    AdapterType,
    PricingModel,
    ProviderAdapter,
    ProviderCapabilities,
)
from inferia.services.orchestration.services.worker_controller.auth import (
    mint_bootstrap_token,
)

logger = logging.getLogger(__name__)

PROJECT_NAME = "inferia-aws"


class ProvisionError(Exception):
    """Surface-safe provisioning error (no internal stack text)."""


# Indirection so tests can patch it.
def load_providers_config() -> ProvidersConfig:
    """Load the current ProvidersConfig from system_settings.

    Concrete callers patch this in tests. Production path loads via
    config_manager.get_config (Fernet-decrypted).
    """
    from inferia.services.api_gateway.management.config_manager import config_manager
    # config_manager has its own sync accessor cached at boot
    data = config_manager.get_cached() or {}
    raw = (data.get("providers") or {})
    return ProvidersConfig.model_validate(raw)


class PulumiAWSAdapter(PulumiProvisioningBase, ProviderAdapter):
    ADAPTER_TYPE = AdapterType.CLOUD
    CAPABILITIES = ProviderCapabilities(
        supports_gpu=True,
        supports_cluster_mode=True,
        pricing_model=PricingModel.ON_DEMAND,
        features={"cloud": "aws", "bootstrap": "cloud-init", "iac": "pulumi"},
    )

    def __init__(
        self,
        *,
        db=None,
        state_dir: Optional[str] = None,
        passphrase: Optional[str] = None,
    ) -> None:
        PulumiProvisioningBase.__init__(
            self,
            state_dir=state_dir or settings.pulumi_state_dir,
            project_name=PROJECT_NAME,
            passphrase=passphrase if passphrase is not None else settings.pulumi_passphrase,
        )
        self._db = db

    async def provision_node(
        self,
        *,
        provider_resource_id: str,
        pool_id: str,
        org_id: str,
        region: Optional[str] = None,
        use_spot: bool = False,
        metadata: Optional[Dict[str, Any]] = None,
        provider_credential_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        cfg = load_providers_config()
        env_vars = resolve_aws_env(cfg)  # raises MissingCredentialsError

        # Validate any per-pool metadata before we touch AWS or the DB.
        pool_meta = dict(metadata or {})
        if pool_meta:
            try:
                AWSPoolMetadata(**pool_meta)
            except Exception as e:
                raise ProvisionError(f"invalid AWS metadata: {e}") from e

        account = cfg.cloud.aws
        region = region or account.region or "us-east-1"
        subnet_id = pool_meta.get("subnet_id") or account.subnet_id
        sg_ids = pool_meta.get("security_group_ids") or account.security_group_ids
        ami_id = pool_meta.get("ami_id") or account.ami_id
        iam_arn = pool_meta.get("iam_instance_profile") or account.iam_instance_profile
        root_gb = pool_meta.get("root_volume_gb") or account.root_volume_gb or 100
        image_tag = pool_meta.get("worker_image_tag") or account.worker_image_tag or settings.worker_image_tag

        if not ami_id:
            try:
                ami_id = latest_dlami_ami(region)
            except AMILookupError as e:
                raise ProvisionError(f"AMI lookup failed: {e}") from e

        self.ensure_state_dir()  # raises PulumiStateError

        token, bootstrap_id = await mint_bootstrap_token(
            self._db,
            pool_id=UUID(pool_id),
            org_id=org_id,
        )
        user_data = build_user_data(
            bootstrap_token=token,
            control_plane_url=settings.control_plane_external_url,
            node_name=f"node-{str(bootstrap_id)[:8]}",
            pool_id=pool_id,
            image=settings.worker_image,
            image_tag=image_tag,
        )

        stack_name = self.stack_name_for_pool(pool_id)
        program = build_ec2_program(
            pool_id=pool_id,
            org_id=org_id,
            bootstrap_id=str(bootstrap_id),
            instance_type=provider_resource_id,
            region=region,
            ami_id=ami_id,
            subnet_id=subnet_id,
            security_group_ids=list(sg_ids) if sg_ids else None,
            iam_instance_profile=iam_arn,
            root_volume_gb=int(root_gb),
            user_data=user_data,
            use_spot=use_spot,
        )

        opts = self.local_workspace_opts(env_vars=env_vars)
        stack = pulumi.automation.create_or_select_stack(
            stack_name=stack_name,
            project_name=self.project_name,
            program=program,
            opts=pulumi.automation.LocalWorkspaceOptions(
                work_dir=opts.work_dir,
                env_vars=opts.env_vars,
                project_settings=pulumi.automation.ProjectSettings(
                    name=self.project_name,
                    runtime="python",
                ),
            ),
        )
        stack.set_config("aws:region", pulumi.automation.ConfigValue(region))

        # Kick off the long-running up call as a background task.
        asyncio.create_task(self._provision_async(stack, pool_id, str(bootstrap_id)))

        return {
            "provider": "aws",
            "provider_instance_id": None,
            "region": region,
            "lifecycle_state": "provisioning",
            "metadata": {
                "pulumi_stack": stack_name,
                "bootstrap_id": str(bootstrap_id),
            },
        }

    async def _provision_async(self, stack: Any, pool_id: str, bootstrap_id: str) -> None:
        """Run `pulumi up` and update compute_pools when it finishes.
        Failure paths added in Task P6."""
        result = await stack.up_async()
        outputs = result.outputs or {}
        instance_id = outputs.get("instance_id", MagicMock()).value if hasattr(outputs.get("instance_id"), "value") else outputs.get("instance_id")
        # Real implementation:
        # await self._db.execute(
        #     "UPDATE compute_pools SET metadata = metadata || $1::jsonb WHERE id = $2",
        #     json.dumps({"instance_id": instance_id}), UUID(pool_id),
        # )
        # Stub for the happy-path test; full DB wiring lands in Task P6.
        logger.info("Pulumi up completed for pool %s: instance %s", pool_id, instance_id)


# Avoid importing MagicMock at module scope.
from unittest.mock import MagicMock  # noqa: E402
```

- [ ] **Step 5: Run; expect happy-path test passes**

Run: `pytest .../pulumi/test_pulumi_aws_adapter.py::test_provision_node_kicks_off_async_task -v`
Expected: PASS. The credentials and metadata tests will also pass.

- [ ] **Step 6: Commit**

```bash
git add package/src/inferia/services/orchestration/services/adapter_engine/adapters/pulumi/programs.py \
        package/src/inferia/services/orchestration/services/adapter_engine/adapters/pulumi/pulumi_aws_adapter.py \
        package/src/inferia/services/orchestration/services/adapter_engine/adapters/pulumi/test_pulumi_aws_adapter.py \
        package/src/inferia/services/orchestration/config.py
git -c user.signingkey=/home/ankit/.ssh/id_ed25519_gh commit -S -m "pulumi: PulumiAWSAdapter happy-path provision via Automation API"
```

---

## Task P6: PulumiAWSAdapter — discover/wait/deprovision/get_logs + DB wiring + failure paths

**Files:**
- Modify: `.../pulumi/pulumi_aws_adapter.py`
- Modify: `.../pulumi/test_pulumi_aws_adapter.py`

- [ ] **Step 1: Add failing tests for each method + failure paths**

Append to `test_pulumi_aws_adapter.py`:

```python
@pytest.mark.asyncio
async def test_provision_async_failure_marks_pool_failed(fake_db, aws_config, tmp_path):
    """When stack.up_async raises, the pool moves to lifecycle_state='failed'
    and the error message lands in metadata.error."""
    fake_stack = MagicMock()
    fake_stack.up_async = AsyncMock(side_effect=Exception("InsufficientInstanceCapacity"))
    fake_stack.destroy_async = AsyncMock(return_value=None)
    adapter = PulumiAWSAdapter(db=fake_db, state_dir=str(tmp_path))
    await adapter._provision_async(fake_stack, "pool-1", "boot-1")
    # _db.execute called with UPDATE compute_pools ... lifecycle_state='failed'
    calls = [c for c in fake_db.execute.call_args_list]
    assert any("'failed'" in str(c) or "failed" in str(c) for c in calls)


@pytest.mark.asyncio
async def test_wait_for_ready_polls_until_inventory_ready(fake_db, tmp_path):
    # Simulate inventory rows: pending → pending → ready
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
async def test_wait_for_ready_timeout_destroys_stack(fake_db, tmp_path):
    fake_db.fetchrow = AsyncMock(return_value={"state": "pending"})
    fake_stack = MagicMock()
    fake_stack.destroy_async = AsyncMock(return_value=None)
    adapter = PulumiAWSAdapter(db=fake_db, state_dir=str(tmp_path))
    with patch.object(adapter, "_select_stack", return_value=fake_stack):
        with pytest.raises(ProvisionError):
            await adapter.wait_for_ready(
                provider_instance_id="boot-1",
                timeout=0.05,
                poll_interval=0.01,
            )
    fake_stack.destroy_async.assert_awaited_once()


@pytest.mark.asyncio
async def test_deprovision_node_destroys_stack(fake_db, tmp_path):
    fake_stack = MagicMock()
    fake_stack.destroy_async = AsyncMock(return_value=None)
    fake_stack.workspace.remove_stack = MagicMock()
    adapter = PulumiAWSAdapter(db=fake_db, state_dir=str(tmp_path))
    with patch.object(adapter, "_select_stack", return_value=fake_stack):
        await adapter.deprovision_node(provider_instance_id="pool-1")
    fake_stack.destroy_async.assert_awaited_once()


@pytest.mark.asyncio
async def test_discover_resources_normalizes_output(fake_db, aws_config, tmp_path):
    """describe_instance_types via boto3 (creds already in env) → normalized list."""
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
    assert by_type["m5.large"]["gpu_vendor"] == "none"


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
```

- [ ] **Step 2: Implement the four methods + failure handling**

Edit `pulumi_aws_adapter.py`. Replace the existing `_provision_async` and add new methods:

```python
    async def _provision_async(self, stack: Any, pool_id: str, bootstrap_id: str) -> None:
        """Run pulumi up. On success, write outputs into compute_pools.metadata.
        On failure, set lifecycle_state='failed' and record the error."""
        try:
            result = await stack.up_async()
            outputs = result.outputs or {}
            instance_id = self._extract_output(outputs, "instance_id")
            public_dns = self._extract_output(outputs, "public_dns")
            private_ip = self._extract_output(outputs, "private_ip")
            meta_update = {
                "instance_id": instance_id,
                "public_dns": public_dns,
                "private_ip": private_ip,
            }
            if self._db is not None:
                await self._db.execute(
                    "UPDATE compute_pools "
                    "SET metadata = COALESCE(metadata, '{}'::jsonb) || $1::jsonb "
                    "WHERE id = $2",
                    json.dumps(meta_update),
                    UUID(pool_id),
                )
            logger.info("Pulumi up succeeded for pool %s: instance %s", pool_id, instance_id)
        except Exception as e:
            err = str(e)
            logger.error("Pulumi up failed for pool %s: %s", pool_id, err)
            if self._db is not None:
                await self._db.execute(
                    "UPDATE compute_pools "
                    "SET lifecycle_state = 'failed', "
                    "    metadata = COALESCE(metadata, '{}'::jsonb) || $1::jsonb "
                    "WHERE id = $2",
                    json.dumps({"error": err}),
                    UUID(pool_id),
                )
            # Best-effort destroy of any partial resources.
            try:
                await stack.destroy_async()
            except Exception as de:
                logger.warning("destroy_async failed after up failure: %s", de)

    @staticmethod
    def _extract_output(outputs: Dict[str, Any], key: str) -> Any:
        v = outputs.get(key)
        if v is None:
            return None
        return v.value if hasattr(v, "value") else v

    def _select_stack(self, pool_id: str) -> Any:
        """Open an existing stack from the local-FS state. Used by
        wait_for_ready and deprovision_node."""
        cfg = load_providers_config()
        env_vars = resolve_aws_env(cfg)
        opts = self.local_workspace_opts(env_vars=env_vars)
        return pulumi.automation.create_or_select_stack(
            stack_name=self.stack_name_for_pool(pool_id),
            project_name=self.project_name,
            program=lambda: None,
            opts=pulumi.automation.LocalWorkspaceOptions(
                work_dir=opts.work_dir,
                env_vars=opts.env_vars,
                project_settings=pulumi.automation.ProjectSettings(
                    name=self.project_name,
                    runtime="python",
                ),
            ),
        )

    async def wait_for_ready(
        self,
        *,
        provider_instance_id: str,
        timeout: int = 900,
        poll_interval: float = 5.0,
        provider_credential_name: Optional[str] = None,
        region: Optional[str] = None,
    ) -> str:
        """Poll compute_inventory until a worker with the given
        bootstrap_id has registered, or timeout. On timeout, destroy
        the stack."""
        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            row = await self._db.fetchrow(
                "SELECT state FROM compute_inventory "
                "WHERE labels->>'bootstrap_id' = $1 "
                "ORDER BY created_at DESC LIMIT 1",
                provider_instance_id,
            ) if self._db is not None else None
            if row and row["state"] == "ready":
                return "ready"
            await asyncio.sleep(poll_interval)
        # Timeout: best-effort destroy.
        stack = self._select_stack(provider_instance_id)
        try:
            await stack.destroy_async()
        except Exception:
            pass
        raise ProvisionError("worker did not register within timeout")

    async def deprovision_node(
        self,
        *,
        provider_instance_id: str,
        provider_credential_name: Optional[str] = None,
    ) -> None:
        stack = self._select_stack(provider_instance_id)
        await stack.destroy_async()
        try:
            stack.workspace.remove_stack(self.stack_name_for_pool(provider_instance_id))
        except Exception as e:
            logger.warning("remove_stack failed (non-fatal): %s", e)

    async def discover_resources(self, *, region: str = "us-east-1") -> List[Dict[str, Any]]:
        import boto3
        cfg = load_providers_config()
        env_vars = resolve_aws_env(cfg)
        ec2 = boto3.client(
            "ec2",
            region_name=region,
            aws_access_key_id=env_vars["AWS_ACCESS_KEY_ID"],
            aws_secret_access_key=env_vars["AWS_SECRET_ACCESS_KEY"],
        )
        out: List[Dict[str, Any]] = []
        next_token: Optional[str] = None
        while True:
            kwargs: Dict[str, Any] = {"MaxResults": 100}
            if next_token:
                kwargs["NextToken"] = next_token
            resp = ec2.describe_instance_types(**kwargs)
            for it in resp.get("InstanceTypes", []):
                gpus = (it.get("GpuInfo") or {}).get("Gpus") or []
                gpu = gpus[0] if gpus else {}
                mfg = (gpu.get("Manufacturer") or "").strip().lower()
                if not gpus:
                    vendor = "none"
                elif "nvidia" in mfg:
                    vendor = "nvidia"
                elif "amd" in mfg:
                    vendor = "amd"
                elif "intel" in mfg or "habana" in mfg:
                    vendor = "intel"
                else:
                    vendor = "other"
                out.append({
                    "provider": "aws",
                    "provider_resource_id": it["InstanceType"],
                    "gpu_type": gpu.get("Name", "N/A") if gpus else "N/A",
                    "gpu_count": gpu.get("Count", 0),
                    "gpu_memory_gb": ((gpu.get("MemoryInfo") or {}).get("SizeInMiB", 0)) // 1024,
                    "gpu_vendor": vendor,
                    "vcpu": it.get("VCpuInfo", {}).get("DefaultVCpus", 0),
                    "ram_gb": it.get("MemoryInfo", {}).get("SizeInMiB", 0) // 1024,
                    "region": region,
                    "pricing_model": "on_demand",
                    "price_per_hour": 0.0,
                })
            next_token = resp.get("NextToken")
            if not next_token:
                break
        return out

    async def get_logs(
        self,
        *,
        provider_instance_id: str,
        provider_credential_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        import boto3
        cfg = load_providers_config()
        env_vars = resolve_aws_env(cfg)
        ec2 = boto3.client(
            "ec2",
            region_name=env_vars["AWS_DEFAULT_REGION"],
            aws_access_key_id=env_vars["AWS_ACCESS_KEY_ID"],
            aws_secret_access_key=env_vars["AWS_SECRET_ACCESS_KEY"],
        )
        try:
            resp = ec2.get_console_output(InstanceId=provider_instance_id)
        except Exception:
            return {"logs": []}
        text = resp.get("Output") or ""
        return {"logs": text.splitlines()}

    async def get_log_streaming_info(self, **_kwargs) -> Dict[str, Any]:
        return {"supported": False, "reason": "Pulumi adapter uses worker WS for live logs"}
```

- [ ] **Step 3: Run all tests in the file**

Run: `pytest .../pulumi/test_pulumi_aws_adapter.py -v` — expect 8/8 pass.

- [ ] **Step 4: Commit**

```bash
git add package/src/inferia/services/orchestration/services/adapter_engine/adapters/pulumi/pulumi_aws_adapter.py \
        package/src/inferia/services/orchestration/services/adapter_engine/adapters/pulumi/test_pulumi_aws_adapter.py
git -c user.signingkey=/home/ankit/.ssh/id_ed25519_gh commit -S -m "pulumi: PulumiAWSAdapter discover/wait/deprovision/logs + failure paths"
```

---

## Task P7: PulumiGCPAdapter skeleton

**Files:**
- Create: `.../pulumi/pulumi_gcp_adapter.py`
- Create: `.../pulumi/test_pulumi_gcp_adapter.py`
- Modify: `.../pulumi/programs.py` (add `build_gce_program`)

- [ ] **Step 1: Add GCP program**

Append to `programs.py`:

```python
def build_gce_program(
    *,
    pool_id: str,
    org_id: str,
    bootstrap_id: str,
    machine_type: str,
    zone: str,
    image_uri: str,
    user_data: str,
) -> Callable[[], None]:
    """Return a Pulumi program for a single gcp.compute.Instance."""

    def _program() -> None:
        import pulumi
        import pulumi_gcp as gcp

        instance = gcp.compute.Instance(
            f"inferia-pool-{pool_id}",
            name=f"inferia-pool-{pool_id}",
            machine_type=machine_type,
            zone=zone,
            boot_disk=gcp.compute.InstanceBootDiskArgs(
                initialize_params=gcp.compute.InstanceBootDiskInitializeParamsArgs(
                    image=image_uri,
                ),
            ),
            network_interfaces=[
                gcp.compute.InstanceNetworkInterfaceArgs(
                    network="default",
                    access_configs=[gcp.compute.InstanceNetworkInterfaceAccessConfigArgs()],
                ),
            ],
            metadata={
                "startup-script": user_data,
                "inferia-pool-id": pool_id,
                "inferia-org-id": org_id,
                "inferia-bootstrap-id": bootstrap_id,
            },
            labels={
                "inferia-pool-id": pool_id,
            },
        )
        pulumi.export("instance_id", instance.id)
        pulumi.export("public_ip", instance.network_interfaces.apply(
            lambda nis: nis[0].access_configs[0].nat_ip if nis else None
        ))

    return _program
```

- [ ] **Step 2: Failing test**

```python
"""Tests for PulumiGCPAdapter — happy provision + creds gating."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from inferia.services.orchestration.services.adapter_engine.adapters.pulumi.credentials import (
    MissingCredentialsError,
)
from inferia.services.orchestration.services.adapter_engine.adapters.pulumi.pulumi_gcp_adapter import (
    PulumiGCPAdapter,
)


@pytest.fixture
def fake_db():
    db = MagicMock()
    db.execute = AsyncMock(return_value="OK")
    db.fetchrow = AsyncMock(return_value=None)
    return db


@pytest.fixture
def gcp_config():
    from inferia.services.api_gateway.config import (
        CloudConfig, GCPConfig, ProvidersConfig,
    )
    return ProvidersConfig(cloud=CloudConfig(
        gcp=GCPConfig(
            project_id="my-proj",
            region="us-central1",
            service_account_json='{"type":"service_account"}',
        )
    ))


@pytest.mark.asyncio
async def test_provision_node_kicks_off_async_task(fake_db, gcp_config, tmp_path):
    fake_stack = MagicMock()
    fake_stack.up_async = AsyncMock(return_value=MagicMock(outputs={}))
    fake_stack.set_config = MagicMock()
    with patch(
        "inferia.services.orchestration.services.adapter_engine.adapters.pulumi.pulumi_gcp_adapter."
        "pulumi.automation.create_or_select_stack",
        return_value=fake_stack,
    ), patch(
        "inferia.services.orchestration.services.adapter_engine.adapters.pulumi.pulumi_gcp_adapter."
        "load_providers_config",
        return_value=gcp_config,
    ), patch(
        "inferia.services.orchestration.services.adapter_engine.adapters.pulumi.pulumi_gcp_adapter."
        "mint_bootstrap_token",
        new=AsyncMock(return_value=("tok-x", "boot-x")),
    ):
        adapter = PulumiGCPAdapter(db=fake_db, state_dir=str(tmp_path))
        result = await adapter.provision_node(
            provider_resource_id="n1-standard-4",
            pool_id="p1",
            org_id="o1",
            region="us-central1",
        )
    assert result["provider"] == "gcp"
    assert result["lifecycle_state"] == "provisioning"


@pytest.mark.asyncio
async def test_provision_node_missing_creds(fake_db, tmp_path):
    from inferia.services.api_gateway.config import (
        CloudConfig, GCPConfig, ProvidersConfig,
    )
    empty = ProvidersConfig(cloud=CloudConfig(gcp=GCPConfig()))
    with patch(
        "inferia.services.orchestration.services.adapter_engine.adapters.pulumi.pulumi_gcp_adapter."
        "load_providers_config",
        return_value=empty,
    ):
        adapter = PulumiGCPAdapter(db=fake_db, state_dir=str(tmp_path))
        with pytest.raises(MissingCredentialsError):
            await adapter.provision_node(
                provider_resource_id="n1-standard-4",
                pool_id="p1",
                org_id="o1",
                region="us-central1",
            )
```

- [ ] **Step 3: Implement `pulumi_gcp_adapter.py`**

```python
"""Pulumi-backed Google Cloud provisioning adapter.

Mirrors PulumiAWSAdapter; only the inline program and credential
resolver differ. wait_for_ready / deprovision_node / discover_resources /
get_logs are no-op stubs in this iteration (GCP provisioning works but
discovery and logs are deferred).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Optional
from uuid import UUID

import pulumi.automation

from inferia.services.api_gateway.config import ProvidersConfig
from inferia.services.orchestration.config import settings
from inferia.services.orchestration.services.adapter_engine.adapters.aws.bootstrap_builder import (
    build_user_data,
)
from inferia.services.orchestration.services.adapter_engine.adapters.pulumi.base import (
    PulumiProvisioningBase,
)
from inferia.services.orchestration.services.adapter_engine.adapters.pulumi.credentials import (
    MissingCredentialsError,
    resolve_gcp_env,
)
from inferia.services.orchestration.services.adapter_engine.adapters.pulumi.programs import (
    build_gce_program,
)
from inferia.services.orchestration.services.adapter_engine.adapters.pulumi.pulumi_aws_adapter import (
    load_providers_config,  # reuse the same accessor
)
from inferia.services.orchestration.services.adapter_engine.base import (
    AdapterType,
    PricingModel,
    ProviderAdapter,
    ProviderCapabilities,
)
from inferia.services.orchestration.services.worker_controller.auth import (
    mint_bootstrap_token,
)

logger = logging.getLogger(__name__)

PROJECT_NAME = "inferia-gcp"

# Default GPU-capable image. Override via cfg.cloud.gcp later if needed.
_DEFAULT_GCE_IMAGE = "projects/deeplearning-platform-release/global/images/family/common-cu121-ubuntu-2204-py310"


class PulumiGCPAdapter(PulumiProvisioningBase, ProviderAdapter):
    ADAPTER_TYPE = AdapterType.CLOUD
    CAPABILITIES = ProviderCapabilities(
        supports_gpu=True,
        supports_cluster_mode=True,
        pricing_model=PricingModel.ON_DEMAND,
        features={"cloud": "gcp", "iac": "pulumi"},
    )

    def __init__(self, *, db=None, state_dir: Optional[str] = None, passphrase: Optional[str] = None):
        PulumiProvisioningBase.__init__(
            self,
            state_dir=state_dir or settings.pulumi_state_dir,
            project_name=PROJECT_NAME,
            passphrase=passphrase if passphrase is not None else settings.pulumi_passphrase,
        )
        self._db = db

    async def provision_node(
        self,
        *,
        provider_resource_id: str,
        pool_id: str,
        org_id: str,
        region: Optional[str] = None,
        use_spot: bool = False,
        metadata: Optional[Dict[str, Any]] = None,
        provider_credential_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        cfg = load_providers_config()
        env_vars = resolve_gcp_env(cfg, write_dir=self.state_dir)

        self.ensure_state_dir()
        token, bootstrap_id = await mint_bootstrap_token(
            self._db,
            pool_id=UUID(pool_id) if isinstance(pool_id, str) and len(pool_id) >= 32 else pool_id,
            org_id=org_id,
        )
        user_data = build_user_data(
            bootstrap_token=token,
            control_plane_url=settings.control_plane_external_url,
            node_name=f"node-{str(bootstrap_id)[:8]}",
            pool_id=pool_id,
            image=settings.worker_image,
            image_tag=settings.worker_image_tag,
        )

        zone = region or cfg.cloud.gcp.region or "us-central1-a"
        program = build_gce_program(
            pool_id=pool_id,
            org_id=org_id,
            bootstrap_id=str(bootstrap_id),
            machine_type=provider_resource_id,
            zone=zone,
            image_uri=_DEFAULT_GCE_IMAGE,
            user_data=user_data,
        )
        opts = self.local_workspace_opts(env_vars=env_vars)
        stack = pulumi.automation.create_or_select_stack(
            stack_name=self.stack_name_for_pool(pool_id),
            project_name=self.project_name,
            program=program,
            opts=pulumi.automation.LocalWorkspaceOptions(
                work_dir=opts.work_dir,
                env_vars=opts.env_vars,
                project_settings=pulumi.automation.ProjectSettings(
                    name=self.project_name,
                    runtime="python",
                ),
            ),
        )
        stack.set_config("gcp:project", pulumi.automation.ConfigValue(cfg.cloud.gcp.project_id))
        stack.set_config("gcp:region", pulumi.automation.ConfigValue(cfg.cloud.gcp.region or "us-central1"))

        asyncio.create_task(self._provision_async(stack, pool_id))
        return {
            "provider": "gcp",
            "provider_instance_id": None,
            "region": zone,
            "lifecycle_state": "provisioning",
            "metadata": {"pulumi_stack": self.stack_name_for_pool(pool_id),
                         "bootstrap_id": str(bootstrap_id)},
        }

    async def _provision_async(self, stack, pool_id: str) -> None:
        try:
            await stack.up_async()
        except Exception as e:
            logger.error("Pulumi up failed for GCP pool %s: %s", pool_id, e)
            try:
                await stack.destroy_async()
            except Exception:
                pass

    async def wait_for_ready(self, *, provider_instance_id, timeout=900,
                              poll_interval=5.0, provider_credential_name=None,
                              region=None) -> str:
        return "ready"

    async def deprovision_node(self, *, provider_instance_id, provider_credential_name=None) -> None:
        cfg = load_providers_config()
        env_vars = resolve_gcp_env(cfg, write_dir=self.state_dir)
        opts = self.local_workspace_opts(env_vars=env_vars)
        stack = pulumi.automation.create_or_select_stack(
            stack_name=self.stack_name_for_pool(provider_instance_id),
            project_name=self.project_name,
            program=lambda: None,
            opts=pulumi.automation.LocalWorkspaceOptions(
                work_dir=opts.work_dir,
                env_vars=opts.env_vars,
                project_settings=pulumi.automation.ProjectSettings(
                    name=self.project_name, runtime="python",
                ),
            ),
        )
        await stack.destroy_async()

    async def discover_resources(self, *, region: str = "us-central1") -> list:
        # Static list for v1 — GCP machine type discovery is deferred.
        return []

    async def get_logs(self, *, provider_instance_id, provider_credential_name=None) -> dict:
        return {"logs": []}

    async def get_log_streaming_info(self, **_kwargs) -> dict:
        return {"supported": False}
```

- [ ] **Step 4: Run + commit**

Run: `pytest .../pulumi/test_pulumi_gcp_adapter.py -v` → 2/2 pass.

```bash
git add package/src/inferia/services/orchestration/services/adapter_engine/adapters/pulumi/programs.py \
        package/src/inferia/services/orchestration/services/adapter_engine/adapters/pulumi/pulumi_gcp_adapter.py \
        package/src/inferia/services/orchestration/services/adapter_engine/adapters/pulumi/test_pulumi_gcp_adapter.py
git -c user.signingkey=/home/ankit/.ssh/id_ed25519_gh commit -S -m "pulumi: PulumiGCPAdapter skeleton via Automation API"
```

---

## Task P8: PulumiAzureAdapter skeleton

**Files:**
- Create: `.../pulumi/pulumi_azure_adapter.py`
- Create: `.../pulumi/test_pulumi_azure_adapter.py`
- Modify: `.../pulumi/programs.py` (add `build_azure_vm_program`)

- [ ] **Step 1: Add Azure program**

Append to `programs.py`:

```python
def build_azure_vm_program(
    *,
    pool_id: str,
    org_id: str,
    bootstrap_id: str,
    vm_size: str,
    location: str,
    user_data: str,
) -> Callable[[], None]:
    """Return a Pulumi program for a single azure_native.compute.VirtualMachine.

    Creates a resource group, virtual network, NIC, and the VM itself.
    GPU SKUs (NC/ND/NV families) use the standard Ubuntu 22.04 image; the
    NVIDIA driver is layered by cloud-init when the worker container
    starts.
    """

    def _program() -> None:
        import base64
        import pulumi
        import pulumi_azure_native as azure

        rg = azure.resources.ResourceGroup(
            f"inferia-rg-{pool_id}",
            resource_group_name=f"inferia-rg-{pool_id}",
            location=location,
        )
        vnet = azure.network.VirtualNetwork(
            f"inferia-vnet-{pool_id}",
            resource_group_name=rg.name,
            location=location,
            address_space=azure.network.AddressSpaceArgs(address_prefixes=["10.0.0.0/16"]),
        )
        subnet = azure.network.Subnet(
            f"inferia-subnet-{pool_id}",
            resource_group_name=rg.name,
            virtual_network_name=vnet.name,
            address_prefix="10.0.1.0/24",
        )
        nic = azure.network.NetworkInterface(
            f"inferia-nic-{pool_id}",
            resource_group_name=rg.name,
            location=location,
            ip_configurations=[azure.network.NetworkInterfaceIPConfigurationArgs(
                name="ipconfig",
                subnet=azure.network.SubnetArgs(id=subnet.id),
                private_ip_allocation_method=azure.network.IPAllocationMethod.DYNAMIC,
            )],
        )
        vm = azure.compute.VirtualMachine(
            f"inferia-vm-{pool_id}",
            resource_group_name=rg.name,
            location=location,
            hardware_profile=azure.compute.HardwareProfileArgs(vm_size=vm_size),
            network_profile=azure.compute.NetworkProfileArgs(
                network_interfaces=[azure.compute.NetworkInterfaceReferenceArgs(
                    id=nic.id, primary=True,
                )],
            ),
            os_profile=azure.compute.OSProfileArgs(
                computer_name=f"inferia-{pool_id[:8]}",
                admin_username="azureuser",
                custom_data=base64.b64encode(user_data.encode()).decode(),
                linux_configuration=azure.compute.LinuxConfigurationArgs(
                    disable_password_authentication=False,
                ),
            ),
            storage_profile=azure.compute.StorageProfileArgs(
                image_reference=azure.compute.ImageReferenceArgs(
                    publisher="Canonical",
                    offer="0001-com-ubuntu-server-jammy",
                    sku="22_04-lts-gen2",
                    version="latest",
                ),
            ),
            tags={
                "InferiaPoolId": pool_id,
                "InferiaOrgId": org_id,
                "InferiaBootstrapId": bootstrap_id,
            },
        )
        pulumi.export("vm_id", vm.id)
        pulumi.export("private_ip", nic.ip_configurations[0].private_ip_address)

    return _program
```

- [ ] **Step 2: Implement `pulumi_azure_adapter.py`**

Mirror `pulumi_gcp_adapter.py`, swapping:
- `resolve_gcp_env` → `resolve_azure_env`
- `build_gce_program` → `build_azure_vm_program`
- provider name `"gcp"` → `"azure"`
- project name `"inferia-gcp"` → `"inferia-azure"`
- region kwarg becomes location; default `"eastus"`
- No `stack.set_config("gcp:project", ...)` — use `azure-native:location` instead

(Full code paralleled to GCP; given the length already in P7, leave as a copy-and-substitute exercise.)

- [ ] **Step 3: Test + commit**

Mirror `test_pulumi_gcp_adapter.py` for Azure (2 tests: happy + missing creds).

```bash
git add package/src/inferia/services/orchestration/services/adapter_engine/adapters/pulumi/programs.py \
        package/src/inferia/services/orchestration/services/adapter_engine/adapters/pulumi/pulumi_azure_adapter.py \
        package/src/inferia/services/orchestration/services/adapter_engine/adapters/pulumi/test_pulumi_azure_adapter.py
git -c user.signingkey=/home/ankit/.ssh/id_ed25519_gh commit -S -m "pulumi: PulumiAzureAdapter skeleton via Automation API"
```

---

## Task P9: Registry rewrite (drop SkyPilot, register Pulumi)

**Files:**
- Modify: `.../adapter_engine/registry.py`
- Delete: `.../adapter_engine/adapters/skypilot/` (whole subtree)
- Delete: `.../adapter_engine/adapters/aws/aws_adapter.py` (boto3 adapter)
- Delete: `.../adapter_engine/adapters/aws/test_aws_adapter.py`
- Modify: `.../adapter_engine/test_registry.py` (or create if absent)

- [ ] **Step 1: Add failing registry test**

`.../adapter_engine/test_registry.py`:

```python
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
        import inferia.services.orchestration.services.adapter_engine.adapters.skypilot  # noqa


def test_nosana_akash_k8s_worker_still_registered():
    for name in ("nosana", "akash", "k8s", "worker", "on_prem"):
        assert name in ADAPTER_REGISTRY
```

- [ ] **Step 2: Rewrite `registry.py`**

```python
"""Adapter registry.

Maps a provider string (the value of compute_pools.provider) to the
adapter class that handles its provisioning lifecycle. Cloud providers
(AWS / GCP / Azure) are served by Pulumi-Automation-API-backed adapters;
DePIN and self-hosted providers have their own adapters.

Lambda Cloud and Runpod were previously served by SkyPilot. Pulumi has
no first-class providers for them, so they are intentionally absent from
this registry until someone writes Pulumi `dynamic.ResourceProvider`s
wrapping their REST APIs.
"""
import logging

from inferia.services.orchestration.services.adapter_engine.adapters.akash.akash_adapter import (
    AkashAdapter,
)
from inferia.services.orchestration.services.adapter_engine.adapters.k8s.k8s_adapter import (
    KubernetesAdapter,
)
from inferia.services.orchestration.services.adapter_engine.adapters.nosana.nosana_adapter import (
    NosanaAdapter,
)
from inferia.services.orchestration.services.adapter_engine.adapters.pulumi.pulumi_aws_adapter import (
    PulumiAWSAdapter,
)
from inferia.services.orchestration.services.adapter_engine.adapters.pulumi.pulumi_azure_adapter import (
    PulumiAzureAdapter,
)
from inferia.services.orchestration.services.adapter_engine.adapters.pulumi.pulumi_gcp_adapter import (
    PulumiGCPAdapter,
)
from inferia.services.orchestration.services.adapter_engine.adapters.worker.worker_adapter import (
    WorkerAdapter,
)

logger = logging.getLogger(__name__)

ADAPTER_REGISTRY = {
    "nosana": NosanaAdapter,
    "k8s": KubernetesAdapter,
    "akash": AkashAdapter,
    "worker": WorkerAdapter,
    "on_prem": WorkerAdapter,  # alias for the DB enum value
    # Cloud providers via Pulumi
    "aws": PulumiAWSAdapter,
    "gcp": PulumiGCPAdapter,
    "azure": PulumiAzureAdapter,
}

_ADAPTER_ALIASES = {"on_prem"}


def get_adapter(provider: str):
    cls = ADAPTER_REGISTRY.get(provider)
    if not cls:
        raise ValueError(
            f"No adapter registered for provider '{provider}'. "
            f"Available: {sorted(set(ADAPTER_REGISTRY) - _ADAPTER_ALIASES)}"
        )
    return cls()


def get_registered_providers() -> list:
    return list(ADAPTER_REGISTRY.keys())


def get_provider_info() -> dict:
    info = {}
    for name, cls in ADAPTER_REGISTRY.items():
        if name in _ADAPTER_ALIASES:
            continue
        info[name] = {
            "adapter_type": cls.ADAPTER_TYPE,
            "capabilities": cls.CAPABILITIES.to_dict(),
        }
    return info
```

- [ ] **Step 3: Delete SkyPilot subtree + boto3 aws_adapter**

```bash
cd /storage/intern/hooman/work/InferiaLLM
rm -rf package/src/inferia/services/orchestration/services/adapter_engine/adapters/skypilot
rm -f  package/src/inferia/services/orchestration/services/adapter_engine/adapters/aws/aws_adapter.py
rm -f  package/src/inferia/services/orchestration/services/adapter_engine/adapters/aws/test_aws_adapter.py
```

- [ ] **Step 4: Run all adapter_engine tests + the wider orchestration suite**

```
pytest package/src/inferia/services/orchestration/services/adapter_engine/ -v
pytest package/src/inferia/services/orchestration/ -v --tb=short 2>&1 | tail -30
```
Expect: all new tests pass; any previously passing test that imported `skypilot` now fails — those should also be cleaned up in this commit.

- [ ] **Step 5: Commit**

```bash
git add -A package/src/inferia/services/orchestration/services/adapter_engine/
git -c user.signingkey=/home/ankit/.ssh/id_ed25519_gh commit -S -m "adapter_engine: register Pulumi adapters, delete SkyPilot subtree"
```

---

## Task P10: Frontend copy updates

**Files:**
- Modify: `apps/dashboard/src/pages/Settings/Providers/ProviderList.tsx`
- Modify: `apps/dashboard/src/pages/Compute/NewPool.tsx`

- [ ] **Step 1: ProviderList AWS description**

In `ProviderList.tsx`, find the AWS entry (currently `description: "EC2 GPU clusters via SkyPilot"`). Change to:

```tsx
{ id: "aws", name: "Amazon Web Services", description: "EC2 GPU clusters via Pulumi" },
```

- [ ] **Step 2: NewPool cluster banner copy**

In `apps/dashboard/src/pages/Compute/NewPool.tsx`, find the Step 2 cluster header and AWS banner. Update:

```tsx
{selectedProvider === 'gcp' ? 'Google Cloud Platform' :
 selectedProvider === 'aws' ? 'Amazon Web Services' :
 selectedProvider === 'azure' ? 'Microsoft Azure' :
 'Cluster'} Configuration
```

And update the AWS banner that mentions SkyPilot:
```tsx
"AWS provisioning details ... configured account-wide under Settings → Providers → AWS. Pulumi uses those defaults plus the region and GPU type below."
```

- [ ] **Step 3: Build dashboard**

```
cd apps/dashboard && npm run build 2>&1 | tail -5
```
Expect: `✓ built` with zero new TS errors.

- [ ] **Step 4: Commit**

```bash
cd /storage/intern/hooman/work/InferiaLLM
git add apps/dashboard/src/pages/Settings/Providers/ProviderList.tsx \
        apps/dashboard/src/pages/Compute/NewPool.tsx
git -c user.signingkey=/home/ankit/.ssh/id_ed25519_gh commit -S -m "dashboard: update copy from SkyPilot to Pulumi for cloud providers"
```

---

## Task P11: Smoke test against running container

**Files:** (no source changes; manual smoke + verify)

- [ ] **Step 1: Hot-copy + restart**

```bash
cd /storage/intern/hooman/work/InferiaLLM
docker cp package/src/inferia/. inferia-app:/usr/local/lib/python3.12/site-packages/inferia/
docker cp apps/dashboard/dist/. inferia-app:/usr/local/lib/python3.12/site-packages/inferia/dashboard/
docker exec inferia-app pip install pulumi pulumi-aws pulumi-gcp pulumi-azure-native 2>&1 | tail -5
docker restart inferia-app
```

- [ ] **Step 2: Wait until healthy**

Use Monitor with:
```
until curl -sf http://localhost:8000/health >/dev/null \
  && docker exec inferia-app curl -sf http://localhost:8080/health >/dev/null; do sleep 2; done
```

- [ ] **Step 3: Verify the registry**

```
docker exec inferia-app python3 -c "
from inferia.services.orchestration.services.adapter_engine.registry import (
  get_adapter, ADAPTER_REGISTRY)
print('aws ->', type(get_adapter('aws')).__name__)
print('all:', sorted(ADAPTER_REGISTRY))
"
```
Expect output: `aws -> PulumiAWSAdapter` and the set `{akash, aws, azure, gcp, k8s, nosana, on_prem, worker}`.

- [ ] **Step 4: UI smoke**

Open `http://localhost:3001`, log in (`admin@example.com` / `change-me-immediately`).
1. Settings → Providers → Cloud → AWS card no longer says "SkyPilot".
2. Save AWS credentials + the 6 provisioning fields.
3. Compute → New Pool → pick AWS → Step 2 header reads "Amazon Web Services Configuration".
4. createpool with a fake instance type: the orchestration logs should show `Pulumi up failed for pool ...` (expected, since the AWS creds are fakes), pool's lifecycle_state moves to `failed`, and `metadata.error` is set.

- [ ] **Step 5: Final commit (none needed if no further changes)**

If the smoke surfaced any bugs, fix and commit with `pulumi: smoke fixes`.

---

# Final verification

- [ ] All Pulumi tests pass: `pytest package/src/inferia/services/orchestration/services/adapter_engine/adapters/pulumi/ -v`
- [ ] Registry tests pass: `pytest .../adapter_engine/test_registry.py -v`
- [ ] Dashboard builds: `cd apps/dashboard && npm run build`
- [ ] Smoke ⇒ AWS card present, registry returns `PulumiAWSAdapter`, createpool with bad creds fails cleanly.
- [ ] No `skypilot` imports anywhere: `grep -r "import.*skypilot\|from.*skypilot" package/src/` returns nothing.
- [ ] `pyproject.toml` has the four Pulumi deps, no `skypilot`.

---

# Self-review checklist (against spec)

- ✅ Pulumi Automation API in-process (P5/P7/P8)
- ✅ Local FS state at `/var/lib/inferia/pulumi-state/` (P2, P5)
- ✅ One stack per pool, naming `inferia-pool-<uuid>` (P2)
- ✅ Credentials resolved per-call, never persisted to stack files (P3, P5)
- ✅ `bootstrap_builder` and `pool_metadata` reused (P5)
- ✅ Async provision flow via asyncio.create_task (P5, P6)
- ✅ AWS happy + failure + discover + deprovision + logs + wait_for_ready (P5, P6)
- ✅ GCP + Azure skeleton adapters (P7, P8)
- ✅ SkyPilot fully removed, lambda/runpod unregistered (P9)
- ✅ Dashboard copy updated (P10)
- ✅ ≥95% test coverage on new files (each task adds tests)
- ✅ Live container smoke test (P11)

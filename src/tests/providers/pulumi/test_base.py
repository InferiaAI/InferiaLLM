"""Tests for PulumiProvisioningBase — state-dir lifecycle and stack naming."""

import pytest

from providers.pulumi.base import (
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


def test_state_dir_unwritable_raises(tmp_path):
    # Make tmp_path read-only; ensure_state_dir must raise PulumiStateError.
    target = tmp_path / "state"
    target.mkdir(mode=0o500)
    base = PulumiProvisioningBase(state_dir=str(target / "child"))
    with pytest.raises(PulumiStateError):
        base.ensure_state_dir()
    # Cleanup so pytest can rmtree
    target.chmod(0o700)


def test_workspace_opts_set_backend_url(tmp_path):
    base = PulumiProvisioningBase(state_dir=str(tmp_path))
    opts = base.local_workspace_opts(env_vars={"AWS_ACCESS_KEY_ID": "x"})
    assert opts.env_vars["PULUMI_BACKEND_URL"] == f"file://{tmp_path}"
    assert opts.env_vars["AWS_ACCESS_KEY_ID"] == "x"
    # PULUMI_CONFIG_PASSPHRASE defaults to empty string so Pulumi doesn't
    # prompt interactively for a passphrase.
    assert opts.env_vars["PULUMI_CONFIG_PASSPHRASE"] == ""

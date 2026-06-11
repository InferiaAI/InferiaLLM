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

"""Pulumi AWS adapter — pure functions, no DB writes.

Pre-refactor (May 2026), this module held a fire-and-forget asyncio task
that ran ``stack.up()`` and wrote outputs to ``compute_pools.metadata``.
That swallowed errors. Post-refactor (T10 of the AWS-EC2-node-allocation
plan), the public entry point for Pulumi work is ``run_pulumi_up_sync``
— a synchronous function that returns ``StackOutputs`` or raises a typed
``ProvisioningError``. The reconciler (T15+) is responsible for wrapping
calls in ``asyncio.to_thread(...)`` and writing outcomes to the
``provisioning_jobs`` table.

The ``PulumiAWSAdapter`` class still exists because the orchestrator
constructs it for ``deprovision_node`` / ``wait_for_ready`` /
``discover_resources`` / ``get_logs``. Its ``provision_node`` and
``provision_cluster`` methods now raise ``NotImplementedError`` —
callers must go through the reconciler instead. ``add_provider_node``
in ``api/nodes.py`` is rewritten in T23 to enqueue a provisioning job
rather than call ``provision_node`` directly.
"""
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

import pulumi.automation
from pulumi.automation import ConcurrentUpdateError

from inferia.services.api_gateway.config import ProvidersConfig
from inferia.services.orchestration.config import settings
from inferia.services.orchestration.services.adapter_engine.adapters.pulumi.base import (
    PulumiProvisioningBase,
)
from inferia.services.orchestration.services.adapter_engine.adapters.pulumi.credentials import (
    resolve_aws_env,
)
from inferia.services.orchestration.services.adapter_engine.base import (
    AdapterType,
    PricingModel,
    ProviderAdapter,
    ProviderCapabilities,
)
from inferia.services.orchestration.services.provisioning.errors import (
    AMINotFoundError,
    InvalidCredentialsError,
    ProvisioningError,
    PulumiCliMissingError,
    PulumiTransientError,
)

logger = logging.getLogger(__name__)

PROJECT_NAME = "inferia-aws"

# Where the cloudflared sidecar (deploy/docker-compose.yml) writes the
# ephemeral public tunnel URL. Read at provision time, NOT at startup,
# so a control-plane restart picks up a freshly-rotated tunnel URL on
# the next provision without needing a separate refresh path.
_TUNNEL_URL_FILE = "/var/lib/inferia/tunnel/url"

# Operator-supplied SSH authorized_keys mounted into the orchestration
# container (see deploy/docker-compose.yml). Each line is one public
# key; when non-empty the EC2 bootstrap installs zsh + writes the
# keys for both `ubuntu` and `root` users.
_SSH_AUTHORIZED_KEYS_FILE = "/var/lib/inferia/ssh/authorized_keys"


def _resolve_ssh_authorized_keys() -> str:
    """Read the operator-supplied authorized_keys file if present.

    Returns the file contents or "" when no file (or unreadable). The
    bootstrap_builder treats empty as "skip SSH setup entirely", so a
    misconfigured mount degrades gracefully.
    """
    try:
        with open(_SSH_AUTHORIZED_KEYS_FILE, "r", encoding="utf-8") as f:
            return f.read()
    except (FileNotFoundError, PermissionError, OSError):
        return ""

# Hosts the EC2 worker can NEVER reach, even though they may be set in
# settings.control_plane_external_url (typically through docker-compose
# defaults). Reject these upfront so we don't burn an EC2 instance only
# to have it hang at the worker_bootstrap phase.
_UNREACHABLE_HOSTS = (
    "localhost",
    "127.0.0.1",
    "0.0.0.0",
    "::1",
    "inferia-app",   # docker-compose service hostname; only resolves inside the compose network
)


def _resolve_control_plane_url() -> Optional[str]:
    """Determine the public URL the cloud worker should call back to.

    Priority:
      1. ``settings.control_plane_external_url`` when it points at a
         public host (i.e. passes ``_validate_control_plane_url``).
         Operators override via INFERIA_CONTROL_PLANE_EXTERNAL_URL.
      2. The URL the cloudflared sidecar writes to ``/var/lib/inferia/tunnel/url``.
    The settings default is the docker-compose service hostname
    ``http://api-gateway:8000``; that fails validation and falls
    through to the sidecar file automatically — so the typical dev
    flow (just bring up the stack with cloudflared) works without
    setting any env var.

    Returns ``None`` when neither produces a public URL; the caller
    fails the prepare phase with a helpful message.
    """
    explicit = (settings.control_plane_external_url or "").strip()
    if explicit and _validate_control_plane_url(explicit) is None:
        return explicit
    try:
        with open(_TUNNEL_URL_FILE, "r", encoding="utf-8") as f:
            url = f.read().strip()
        return url or None
    except (FileNotFoundError, PermissionError, OSError):
        return None


def _validate_control_plane_url(url: Optional[str]) -> Optional[str]:
    """Return an error message if the URL is not reachable from the public
    internet (i.e. would leave the worker stuck at worker_bootstrap), or
    None when it looks valid.

    Catches both the obvious bad hosts (localhost, 127.0.0.1, the
    docker-compose service names we know about) AND any unqualified
    hostname (no dot in the host portion). Public DNS hostnames always
    contain at least one dot — anything else is a docker / k8s service
    name that an EC2 instance can never resolve.
    """
    if not url:
        return (
            "CONTROL_PLANE_EXTERNAL_URL is not configured. Cloud workers cannot "
            "phone home. Either set the INFERIA_CONTROL_PLANE_EXTERNAL_URL env "
            "var on the control plane, or start the bundled `cloudflared` "
            "service (see deploy/docker-compose.yml) to auto-generate a public "
            "tunnel URL."
        )
    lowered = url.lower()
    if not (lowered.startswith("http://") or lowered.startswith("https://")):
        return f"CONTROL_PLANE_EXTERNAL_URL={url!r} must include a scheme (http:// or https://)."
    # Pull out the host portion ("host" or "host:port") between "//" and the
    # first "/" or end-of-string.
    after_scheme = lowered.split("//", 1)[1]
    host_with_port = after_scheme.split("/", 1)[0]
    host = host_with_port.split(":", 1)[0]
    if host in _UNREACHABLE_HOSTS:
        return (
            f"CONTROL_PLANE_EXTERNAL_URL={url!r} points at {host!r}, which "
            "is not reachable from a cloud EC2 instance. Use a public "
            "hostname (ngrok / cloudflared / your routable DNS) instead."
        )
    # A public DNS hostname always has a dot (`example.com`,
    # `tunnel.trycloudflare.com`); IPv4 addresses have dots too. Anything
    # else is a docker / k8s service name and cannot be resolved from EC2.
    # IPv6 hostnames would contain ':' which we already split out.
    if "." not in host:
        return (
            f"CONTROL_PLANE_EXTERNAL_URL={url!r} uses an unqualified hostname "
            f"({host!r}). A cloud EC2 instance cannot resolve it. Set "
            "INFERIA_CONTROL_PLANE_EXTERNAL_URL to a public URL (e.g. via the "
            "cloudflared sidecar in deploy/docker-compose.yml)."
        )
    return None

# Map semantic GPU names → a sensible default EC2 instance type. Defensive
# layer for callers (dashboards, scripts) that pass a GPU name like "T4"
# where AWS expects an instance type like "g4dn.xlarge". A real instance
# type always contains a '.'; everything that doesn't is treated as a GPU
# name candidate. Choose the smallest/cheapest variant per family so this
# is safe to apply silently on smoke tests; operators wanting bigger
# variants pass the full instance type explicitly.
_GPU_NAME_TO_INSTANCE: Dict[str, str] = {
    "T4":    "g4dn.xlarge",
    "A10G":  "g5.xlarge",
    "L4":    "g6.xlarge",
    "L40S":  "g6e.xlarge",
    "V100":  "p3.2xlarge",
    "A100":  "p4d.24xlarge",
    "H100":  "p5.48xlarge",
    "H200":  "p5e.48xlarge",
}


def _resolve_instance_type(value: str) -> tuple[str, Optional[str]]:
    """Return (instance_type, mapped_from_gpu_name_or_None).

    If `value` already looks like an EC2 instance type (contains '.'),
    pass it through. Otherwise look it up in the GPU-name table. If the
    name isn't recognized, return it unchanged — Pulumi will surface
    AWS's InvalidParameterValue error in pulumi_up/failed, which the
    new UX captures cleanly.
    """
    if not value or "." in value:
        return value, None
    mapped = _GPU_NAME_TO_INSTANCE.get(value.upper())
    if mapped:
        return mapped, value
    return value, None


class ProvisionError(Exception):
    """Surface-safe provisioning error (no internal stack text).

    Kept for callers that still raise/catch this type via the surviving
    adapter methods (wait_for_ready / deprovision_node fallbacks). New
    code should use the typed hierarchy in
    ``inferia.services.orchestration.services.provisioning.errors``
    instead.
    """


async def load_providers_config() -> ProvidersConfig:
    """Load the current ProvidersConfig from system_settings.

    Opens a short-lived AsyncSession against the gateway DB, reads the
    Fernet-decrypted providers blob, returns it as a Pydantic
    ProvidersConfig. Indirection lives here so tests can monkey-patch
    this function and skip the DB entirely.
    """
    from inferia.services.api_gateway.db.database import AsyncSessionLocal
    from inferia.services.api_gateway.management.config_manager import config_manager
    async with AsyncSessionLocal() as db:
        data = await config_manager.load_config(db) or {}
    raw = data.get("providers") or {}
    return ProvidersConfig.model_validate(raw)


# ---------------------------------------------------------------------------
# Pure Pulumi-up entry point (T10).
#
# The reconciler calls ``run_pulumi_up_sync`` from inside
# ``asyncio.to_thread(...)``. It owns the DB writes, retry policy, and
# lease ownership; this function only knows how to drive ``pulumi up``
# and translate AWS errors into the typed exception hierarchy from
# ``inferia.services.orchestration.services.provisioning.errors``.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StackOutputs:
    """What the reconciler stores into ``provisioning_jobs.pulumi_stack_outputs``.

    Every field is optional because Pulumi outputs can be missing on a
    partially-created stack (e.g. when AWS returned an error mid-way and
    we are inspecting the rollback state). Callers MUST handle ``None``.
    """

    instance_id: str | None
    public_dns: str | None
    region: str | None
    ami_id: str | None

    @classmethod
    def from_pulumi_outputs(cls, outputs: dict[str, Any]) -> "StackOutputs":
        def _v(key: str) -> str | None:
            ref = outputs.get(key)
            if ref is None:
                return None
            return getattr(ref, "value", ref)
        return cls(
            instance_id=_v("instance_id"),
            public_dns=_v("public_dns"),
            region=_v("region"),
            ami_id=_v("ami_id"),
        )


def _make_stack(
    *,
    stack_name: str,
    program: Callable,
    env: dict[str, str],
    state_dir: str | None = None,
    project_name: str = PROJECT_NAME,
):
    """Wraps ``pulumi.automation.create_or_select_stack`` with our
    local-backend env. Extracted so tests can mock it; production calls
    into ``pulumi.automation`` here.

    ``env`` carries the AWS credentials (resolve_aws_env). Pulumi-side
    env vars (``PULUMI_BACKEND_URL``, ``PULUMI_CONFIG_PASSPHRASE``,
    ``PULUMI_HOME``) are merged in from the process environment if not
    already in ``env`` so the local-backend Pulumi sees them. Without
    these the stack would land on Pulumi cloud (or fail), and the
    state wouldn't be reachable by the later deprovision_node call.

    ``state_dir`` overrides the workspace working directory; if
    ``None``, Pulumi creates a fresh temp dir per call (correct only
    for tests). Production callers (PulumiUpHandler via the reconciler
    in T15) MUST pass a persistent ``state_dir`` so subsequent
    ``deprovision_node`` calls can find the stack.

    ``project_name`` is required by ``create_or_select_stack`` for
    inline programs; defaults to the module-level ``PROJECT_NAME``
    constant (``"inferia-aws"``) — the same name used by the surviving
    ``_select_stack`` method so a stack created here is reopen-able
    there.

    The wrapped call may raise ``FileNotFoundError`` when the ``pulumi``
    CLI binary isn't on PATH (memory:
    feedback_pulumi_cli_binary_required) — ``run_pulumi_up_sync`` catches
    that and re-raises as ``PulumiCliMissingError``.
    """
    from pulumi import automation as auto
    # Merge Pulumi backend env from the process if the caller didn't supply.
    full_env = dict(env)
    for k in ("PULUMI_BACKEND_URL", "PULUMI_CONFIG_PASSPHRASE", "PULUMI_HOME"):
        if k not in full_env and (v := os.environ.get(k)):
            full_env[k] = v
    workspace_opts = auto.LocalWorkspaceOptions(
        env_vars=full_env,
        work_dir=state_dir,
        project_settings=auto.ProjectSettings(
            name=project_name, runtime="python",
        ),
    )
    return auto.create_or_select_stack(
        stack_name=stack_name,
        project_name=project_name,
        program=program,
        opts=workspace_opts,
    )


def _run_pulumi_op_with_lock_recovery(op, *, stack, stack_name, op_name):
    """Run a pulumi Automation op (stack.up/stack.destroy). If it fails with a
    STALE lock (ConcurrentUpdateError — a prior pulumi process died mid-run
    under host memory pressure, leaving the file-backend lock), clear it with
    stack.cancel() and retry ONCE.

    Per-node stacks are never operated on concurrently (single-instance
    reconciler via advisory lock + one job per node), so a lock encountered
    here is always stale, making cancel() safe. Without this, every reconciler
    retry hit the same died-process lock → infinite loop, the node never
    purged + the pool never finalized (the EC2 was already terminated; only
    the DB teardown stalled).
    """
    try:
        return op()
    except ConcurrentUpdateError as e:
        logger.warning(
            "pulumi %s: stale lock on stack %s; running cancel()+retry: %s",
            op_name, stack_name, e,
        )
        try:
            stack.cancel()
        except Exception as ce:
            logger.warning(
                "pulumi cancel() failed for %s (continuing to retry): %s",
                stack_name, ce,
            )
        return op()  # retry exactly once; a second ConcurrentUpdateError propagates


def run_pulumi_up_sync(
    *,
    stack_name: str,
    program: Callable[[], None],
    env: dict[str, str],
    state_dir: str | None = None,
    project_name: str = PROJECT_NAME,
) -> StackOutputs:
    """Run ``pulumi up`` synchronously and return the named outputs.

    Raises a typed ``ProvisioningError`` on known failures; the
    reconciler's classifier maps everything else (including
    ``pulumi.automation`` internals) to UNCLASSIFIED PERMANENT.

    ``state_dir`` is forwarded to ``_make_stack`` so production
    callers (PulumiUpHandler in T15) can pin Pulumi to a persistent
    working directory; without it, deprovision_node would not be
    able to reopen the stack. ``project_name`` defaults to the
    module-level ``PROJECT_NAME`` so a stack created here is reachable
    via ``PulumiAWSAdapter._select_stack`` later.

    This function MUST stay sync. The reconciler wraps it in
    ``asyncio.to_thread(...)`` because the Pulumi Python SDK has no
    ``up_async`` (memory: feedback_pulumi_python_sdk_sync).
    """
    try:
        stack = _make_stack(
            stack_name=stack_name,
            program=program,
            env=env,
            state_dir=state_dir,
            project_name=project_name,
        )
    except FileNotFoundError as e:
        # `pulumi` binary not on PATH — classic deploy-time failure
        # (memory: feedback_pulumi_cli_binary_required).
        raise PulumiCliMissingError(
            f"pulumi binary missing: {e}",
        ) from e

    try:
        result = _run_pulumi_op_with_lock_recovery(
            stack.up, stack=stack, stack_name=stack_name, op_name="up",
        )
    except ProvisioningError:
        # The classifier (or upstream callers) already chose a code.
        raise
    except Exception as e:
        msg = str(e).lower()
        # Heuristic mapping for AWS errors that surface through Pulumi's
        # generic exception type. Classifier handles unknown cases via
        # UNCLASSIFIED PERMANENT.
        if "authfailure" in msg or "credentials" in msg or "unauthorized" in msg:
            raise InvalidCredentialsError(str(e)) from e
        if "invalidamiid.notfound" in msg or "image id" in msg:
            raise AMINotFoundError(str(e)) from e
        if "throttling" in msg or "requestlimitexceeded" in msg:
            raise PulumiTransientError(str(e)) from e
        raise  # let the classifier deal with UNCLASSIFIED

    return StackOutputs.from_pulumi_outputs(result.outputs or {})


def run_pulumi_destroy_sync(
    *,
    stack_name: str,
    program: Callable[[], None],
    env: dict[str, str],
    state_dir: str | None = None,
    project_name: str = PROJECT_NAME,
) -> None:
    """Run ``pulumi destroy`` synchronously. Idempotent — destroying a
    stack that doesn't exist is treated as success.

    Used by the reconciler's CancelHandler (T17) when a user deletes a
    node mid-provisioning. The reconciler wraps this in
    ``asyncio.to_thread(...)`` because the Pulumi Python SDK has no
    ``destroy_async`` (memory: feedback_pulumi_python_sdk_sync).

    Raises ``PulumiCliMissingError`` when the ``pulumi`` binary isn't on
    PATH (memory: feedback_pulumi_cli_binary_required). Any other
    exception that doesn't look like a "no stack" error propagates so
    the reconciler's classifier can decide retry vs fail.
    """
    try:
        stack = _make_stack(
            stack_name=stack_name,
            program=program,
            env=env,
            state_dir=state_dir,
            project_name=project_name,
        )
    except FileNotFoundError as e:
        raise PulumiCliMissingError(f"pulumi binary missing: {e}") from e
    try:
        _run_pulumi_op_with_lock_recovery(
            stack.destroy, stack=stack, stack_name=stack_name, op_name="destroy",
        )
    except Exception as e:
        if "no stack named" in str(e).lower():
            return
        raise


# ---------------------------------------------------------------------------
# Adapter — surviving lifecycle helpers (deprovision, wait_for_ready,
# discover_resources, get_logs). The reconciler owns provision_node,
# so the methods that previously drove pulumi.up() are now stubs that
# raise NotImplementedError pointing at the reconciler.
# ---------------------------------------------------------------------------


class PulumiAWSAdapter(PulumiProvisioningBase, ProviderAdapter):
    ADAPTER_TYPE = AdapterType.CLOUD
    CAPABILITIES = ProviderCapabilities(
        supports_multi_gpu=True,
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

    async def provision_node(self, **_kwargs: Any) -> Dict[str, Any]:
        """Deprecated since T10. The reconciler owns provisioning.

        Callers used to hit this and watch lifecycle_state via the DB.
        After T10, ``run_pulumi_up_sync`` (module-level) is the only
        Pulumi entry point and the reconciler in
        ``services/provisioning/`` drives it. T23 rewrites the only
        production caller (``api/nodes.py::add_provider_node``) to
        enqueue a provisioning job instead.
        """
        raise NotImplementedError(
            "PulumiAWSAdapter.provision_node was removed in T10. "
            "Enqueue a provisioning job via the reconciler instead "
            "(see services/orchestration/services/provisioning/). The "
            "module-level helper run_pulumi_up_sync is the only "
            "Pulumi entry point now."
        )

    async def provision_cluster(self, **_kwargs: Any) -> Dict[str, Any]:
        """Deprecated since T10. The single-node-per-pool model is now
        handled end-to-end by the reconciler — no separate cluster
        codepath. See ``provision_node`` docstring."""
        raise NotImplementedError(
            "PulumiAWSAdapter.provision_cluster was removed in T10. "
            "Enqueue a provisioning job via the reconciler instead."
        )

    async def _select_stack(self, pool_id: str) -> Any:
        """Open an existing stack (no program) for wait_for_ready/deprovision.

        Async because it has to await the DB-backed providers config to
        rebuild the AWS env vars Pulumi will inherit.
        """
        cfg = await load_providers_config()
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
                    name=self.project_name, runtime="python",
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
        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            row = None
            if self._db is not None:
                row = await self._db.fetchrow(
                    "SELECT state FROM compute_inventory "
                    "WHERE labels->>'bootstrap_id' = $1 "
                    "ORDER BY created_at DESC LIMIT 1",
                    provider_instance_id,
                )
            if row and row["state"] == "ready":
                return "ready"
            await asyncio.sleep(poll_interval)
        stack = await self._select_stack(provider_instance_id)
        try:
            await asyncio.to_thread(stack.destroy)
        except Exception:
            pass
        raise ProvisionError("worker did not register within timeout")

    async def deprovision_node(
        self,
        *,
        provider_instance_id: str,
        provider_credential_name: Optional[str] = None,
    ) -> None:
        stack = await self._select_stack(provider_instance_id)
        await asyncio.to_thread(stack.destroy)
        try:
            stack.workspace.remove_stack(self.stack_name_for_pool(provider_instance_id))
        except Exception as e:
            logger.warning("remove_stack failed (non-fatal): %s", e)

    async def discover_resources(self, *, region: str = "us-east-1") -> List[Dict[str, Any]]:
        import boto3
        cfg = await load_providers_config()
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
        cfg = await load_providers_config()
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

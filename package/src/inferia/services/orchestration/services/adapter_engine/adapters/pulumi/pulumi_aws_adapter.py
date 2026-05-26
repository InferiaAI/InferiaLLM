"""Pulumi-backed AWS EC2 provisioning adapter.

provision_node returns immediately with lifecycle_state='provisioning'
and schedules an asyncio background task that calls asyncio.to_thread(stack.up)().
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
from inferia.services.orchestration.services.adapter_engine.adapters.pulumi.progress_writer import (
    ProgressWriter,
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
    """Surface-safe provisioning error (no internal stack text)."""


class _NoopWriter:
    """Drop-in no-op for callers that don't provide a progress writer
    (e.g. the legacy lazy-deploy path)."""
    async def write_async(self, *a, **kw): pass
    def write(self, *a, **kw): pass


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
        # Holds a strong reference to the background _provision_async task so
        # callers can await it and CPython cannot GC it prematurely.
        self._last_provision_task: Optional[asyncio.Task] = None

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
        progress_writer: Any = None,
    ) -> Dict[str, Any]:
        writer = progress_writer or _NoopWriter()

        await writer.write_async("prepare", "running")

        # Resolve the public URL the EC2 worker will call back to. If this
        # is empty / points at localhost / a docker-internal hostname the
        # worker will silently hang at worker_bootstrap — fail prepare now
        # with a helpful message instead of burning an EC2 instance.
        control_plane_url = _resolve_control_plane_url()
        cp_err = _validate_control_plane_url(control_plane_url)
        if cp_err:
            await writer.write_async("prepare", "failed", cp_err)
            raise ProvisionError(cp_err)
        await writer.write_async(
            "prepare", "log",
            f"control plane URL: {control_plane_url}",
        )

        # Defensive: callers occasionally pass a GPU name ("T4") where an
        # EC2 instance type ("g4dn.xlarge") is required. Map it before
        # building the Pulumi program so AWS doesn't return
        # InvalidParameterValue. Emit a prepare/log so the dashboard
        # shows the substitution.
        resolved_type, mapped_from = _resolve_instance_type(provider_resource_id)
        if mapped_from:
            await writer.write_async(
                "prepare", "log",
                f"mapped GPU name {mapped_from!r} -> EC2 instance type {resolved_type!r}",
            )
            provider_resource_id = resolved_type

        try:
            cfg = await load_providers_config()
            env_vars = resolve_aws_env(cfg)  # raises MissingCredentialsError

            pool_meta = dict(metadata or {})
            if pool_meta:
                try:
                    AWSPoolMetadata(**pool_meta)
                except Exception as e:
                    await writer.write_async("prepare", "failed", str(e))
                    raise ProvisionError(f"invalid AWS metadata: {e}") from e

            account = cfg.cloud.aws
            region = region or account.region or "us-east-1"
            subnet_id = pool_meta.get("subnet_id") or account.subnet_id
            sg_ids = pool_meta.get("security_group_ids") or account.security_group_ids
            ami_id = pool_meta.get("ami_id") or account.ami_id
            iam_arn = pool_meta.get("iam_instance_profile") or account.iam_instance_profile
            root_gb = pool_meta.get("root_volume_gb") or account.root_volume_gb or 100
            image_tag = (
                pool_meta.get("worker_image_tag")
                or account.worker_image_tag
                or settings.worker_image_tag
            )

        except ProvisionError:
            raise
        except Exception as e:
            await writer.write_async("prepare", "failed", str(e))
            raise

        await writer.write_async("prepare", "succeeded")

        if not ami_id:
            # CPU-only instances (t/m/c/r families) don't need the NVIDIA
            # DLAMI — fall back to plain Ubuntu 22.04, which boots faster
            # and avoids "instance type doesn't support GPU AMI" errors.
            from inferia.services.orchestration.services.adapter_engine.adapters.pulumi.ami import (
                PLAIN_UBUNTU_PARAMETER,
            )
            is_gpu_family = provider_resource_id.split(".")[0].lower() in {
                "g5", "g5g", "g6", "g6e", "g6f", "g4dn", "g4ad", "p4d", "p4de",
                "p5", "p5e", "p5en", "p3", "p3dn", "p2", "dl1", "dl2q", "trn1",
                "trn1n", "trn2",
            }
            param = None if is_gpu_family else PLAIN_UBUNTU_PARAMETER
            await writer.write_async("ami_lookup", "running")
            try:
                ami_id = latest_dlami_ami(
                    region,
                    aws_access_key_id=env_vars["AWS_ACCESS_KEY_ID"],
                    aws_secret_access_key=env_vars["AWS_SECRET_ACCESS_KEY"],
                    parameter_name=param,
                )
                await writer.write_async("ami_lookup", "succeeded", ami_id)
            except AMILookupError as e:
                await writer.write_async("ami_lookup", "failed", str(e))
                raise ProvisionError(f"AMI lookup failed: {e}") from e

        # When constructed via the registry the adapter has no db handle —
        # mint the bootstrap token through a short-lived asyncpg connection
        # opened on demand against the orchestration's POSTGRES_DSN.
        db_conn = self._db
        owned_conn = False
        if db_conn is None:
            import asyncpg
            db_conn = await asyncpg.connect(dsn=settings.postgres_dsn)
            owned_conn = True
        try:
            token, bootstrap_id = await mint_bootstrap_token(
                db_conn,
                pool_id=UUID(pool_id) if isinstance(pool_id, str) else pool_id,
                org_id=org_id,
            )
            # Per-pool inference token. The worker container exits with
            # "INFERENCE_TOKEN is required" without it. Generate-or-fetch
            # via the existing pool_repo SQL pattern.
            import secrets
            pool_uuid = UUID(pool_id) if isinstance(pool_id, str) else pool_id
            inference_token = await db_conn.fetchval(
                "UPDATE compute_pools "
                "SET inference_token = COALESCE(inference_token, $2), "
                "    updated_at = now() "
                "WHERE id = $1 "
                "RETURNING inference_token",
                pool_uuid,
                secrets.token_urlsafe(32),
            )
            # Pre-set the placeholder's node_name to the value build_user_data
            # bakes into the cloud-init script. This needs to land BEFORE
            # cloud-init runs on the EC2 (the worker's register POST is keyed
            # on pool_id+node_name; a missing node_name on the placeholder
            # would cause the worker to insert a fresh row instead of
            # updating the placeholder in place).
            expected_node_name = f"node-{str(bootstrap_id)[:8]}"
            await db_conn.execute(
                "UPDATE compute_inventory "
                "SET node_name = $1, updated_at = now() "
                "WHERE pool_id = $2 AND provider_instance_id LIKE 'placeholder:%'",
                expected_node_name,
                pool_uuid,
            )
        finally:
            if owned_conn:
                try:
                    await db_conn.close()
                except Exception:
                    pass

        ssh_keys = _resolve_ssh_authorized_keys()
        if ssh_keys.strip():
            await writer.write_async(
                "prepare", "log",
                f"SSH provisioning: {len(ssh_keys.strip().splitlines())} key(s) for ubuntu+root",
            )
        user_data = build_user_data(
            bootstrap_token=token,
            control_plane_url=control_plane_url,
            node_name=f"node-{str(bootstrap_id)[:8]}",
            pool_id=pool_id,
            image=settings.worker_image,
            image_tag=image_tag,
            inference_token=inference_token,
            ssh_authorized_keys=ssh_keys,
        )

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

        self.ensure_state_dir()  # raises PulumiStateError

        await writer.write_async("pulumi_init", "running")
        try:
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
            stack.set_config("aws:region", pulumi.automation.ConfigValue(region))
        except Exception as e:
            await writer.write_async("pulumi_init", "failed", str(e))
            raise
        await writer.write_async("pulumi_init", "succeeded")

        await writer.write_async("pulumi_up", "running")
        self._last_provision_task = asyncio.create_task(
            self._provision_async(stack, pool_id, str(bootstrap_id), writer)
        )

        return {
            "provider": "aws",
            "provider_instance_id": None,
            "region": region,
            "lifecycle_state": "provisioning",
            "metadata": {
                "pulumi_stack": self.stack_name_for_pool(pool_id),
                "bootstrap_id": str(bootstrap_id),
            },
        }

    async def provision_cluster(
        self,
        *,
        cluster_name: str,
        gpu_type: str,
        gpu_count: int,
        region: Optional[str] = None,
        use_spot: bool = False,
        provider_credential_name: Optional[str] = None,
        pool_id: Optional[str] = None,
        org_id: Optional[str] = None,
        **_ignored: Any,
    ) -> Dict[str, Any]:
        """Provision a 1-instance "cluster" — one EC2 instance per pool.

        The compute_pool_manager calls this for cloud providers with
        supports_cluster_mode=True. We translate the cluster-shaped args
        into a provision_node call. gpu_type carries the EC2 instance type
        (the dashboard's resource-card path puts the real instance_id
        like 't3.micro' / 'g5.xlarge' in allowed_gpu_types[0], which the
        manager passes here as gpu_type).
        """
        if not pool_id or not org_id:
            raise ProvisionError(
                "provision_cluster requires pool_id and org_id — update the manager"
            )
        result = await self.provision_node(
            provider_resource_id=gpu_type,
            pool_id=pool_id,
            org_id=org_id,
            region=region,
            use_spot=use_spot,
            provider_credential_name=provider_credential_name,
        )
        return {
            "cluster_id": cluster_name,
            "hostname": result.get("metadata", {}).get("public_dns", ""),
            "ip_address": result.get("metadata", {}).get("private_ip", ""),
            "provider_instance_id": result.get("provider_instance_id"),
        }

    async def _provision_async(
        self,
        stack: Any,
        pool_id: str,
        bootstrap_id: str,
        writer: Any = None,
    ) -> None:
        """Run pulumi up. On success, write outputs into compute_pools.metadata.
        On failure, set lifecycle_state='failed' and record the error."""
        writer = writer or _NoopWriter()

        def _on_event(ev):
            try:
                kind = next(
                    (k for k in ("resource_pre_event", "res_outputs_event",
                                 "diagnostic_event", "summary_event")
                     if hasattr(ev, k) and getattr(ev, k) is not None),
                    "engine_event",
                )
                payload = str(getattr(ev, kind, ev))
                writer.write("pulumi_up", "log", f"{kind}: {payload}")
            except Exception:
                pass

        try:
            result = await asyncio.to_thread(stack.up, on_event=_on_event)
            outputs = result.outputs or {}
            instance_id = self._extract_output(outputs, "instance_id")
            public_dns  = self._extract_output(outputs, "public_dns")
            private_ip  = self._extract_output(outputs, "private_ip")
            meta_update = {
                "instance_id": instance_id,
                "public_dns":  public_dns,
                "private_ip":  private_ip,
            }
            await writer.write_async("pulumi_up", "succeeded", instance_id)
            await writer.write_async("ec2_running", "succeeded", public_dns)
            if self._db is not None:
                await self._db.execute(
                    "UPDATE compute_pools "
                    "SET metadata = COALESCE(metadata, '{}'::jsonb) || $1::jsonb "
                    "WHERE id = $2",
                    json.dumps(meta_update),
                    UUID(pool_id),
                )
                # Promote the placeholder inventory row to point at the real
                # EC2 instance. The worker's later register_worker call upserts
                # on (provider, provider_instance_id) and finds the same row,
                # flipping state -> ready.
                if instance_id:
                    # Predict the node_name the worker will register with
                    # so its upsert finds this placeholder (matches on
                    # pool_id + node_name) instead of inserting a fresh
                    # row. Format must mirror build_user_data:
                    #   node_name = f"node-{bootstrap_id[:8]}"
                    expected_node_name = f"node-{str(bootstrap_id)[:8]}"
                    await self._db.execute(
                        "UPDATE compute_inventory "
                        "SET provider_instance_id = $1, hostname = $2, "
                        "    node_name = $3, updated_at = now() "
                        "WHERE pool_id = $4 AND provider_instance_id LIKE 'placeholder:%'",
                        instance_id,
                        public_dns or "",
                        expected_node_name,
                        UUID(pool_id),
                    )
            await writer.write_async("worker_bootstrap", "running")
            logger.info("Pulumi up succeeded for pool %s: instance %s",
                        pool_id, instance_id)
        except Exception as e:
            err = str(e)
            await writer.write_async("pulumi_up", "failed", err)
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
                await self._db.execute(
                    "UPDATE compute_inventory "
                    "SET state = 'terminated', updated_at = now(), "
                    "    metadata = COALESCE(metadata, '{}'::jsonb) || $1::jsonb "
                    "WHERE pool_id = $2 AND provider_instance_id LIKE 'placeholder:%'",
                    json.dumps({"failure_reason": err}),
                    UUID(pool_id),
                )
            try:
                await asyncio.to_thread(stack.destroy)
            except Exception as de:
                logger.warning("destroy failed after up failure: %s", de)

    @staticmethod
    def _extract_output(outputs: Dict[str, Any], key: str) -> Any:
        v = outputs.get(key)
        if v is None:
            return None
        return v.value if hasattr(v, "value") else v

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

"""Pulumi-backed Google Cloud provisioning adapter.

Mirrors PulumiAWSAdapter; only the inline program and credential
resolver differ. wait_for_ready / deprovision_node / discover_resources /
get_logs are minimal stubs in this iteration — GCP provisioning works
but discovery and log streaming are deferred.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Optional
from uuid import UUID

import pulumi.automation

from inferia.services.orchestration.config import settings
from inferia.services.orchestration.services.adapter_engine.adapters.aws.bootstrap_builder import (
    build_user_data,
)
from inferia.services.orchestration.services.adapter_engine.adapters.pulumi.base import (
    PulumiProvisioningBase,
)
from inferia.services.orchestration.services.adapter_engine.adapters.pulumi.credentials import (
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

# Default GPU-capable image. The Deep Learning images carry CUDA
# pre-installed; the bootstrap cloud-init script layers Docker + the
# worker container.
_DEFAULT_GCE_IMAGE = (
    "projects/deeplearning-platform-release/global/images/family/"
    "common-cu121-ubuntu-2204-py310"
)


class PulumiGCPAdapter(PulumiProvisioningBase, ProviderAdapter):
    ADAPTER_TYPE = AdapterType.CLOUD
    CAPABILITIES = ProviderCapabilities(
        supports_multi_gpu=True,
        supports_cluster_mode=True,
        pricing_model=PricingModel.ON_DEMAND,
        features={"cloud": "gcp", "bootstrap": "startup-script", "iac": "pulumi"},
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
        env_vars = resolve_gcp_env(cfg, write_dir=self.state_dir)

        self.ensure_state_dir()
        token, bootstrap_id = await mint_bootstrap_token(
            self._db,
            pool_id=UUID(pool_id) if isinstance(pool_id, str) else pool_id,
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
            "metadata": {
                "pulumi_stack": self.stack_name_for_pool(pool_id),
                "bootstrap_id": str(bootstrap_id),
            },
        }

    async def _provision_async(self, stack, pool_id: str) -> None:
        try:
            await stack.up_async()
            logger.info("Pulumi up succeeded for GCP pool %s", pool_id)
        except Exception as e:
            logger.error("Pulumi up failed for GCP pool %s: %s", pool_id, e)
            try:
                await stack.destroy_async()
            except Exception:
                pass

    async def wait_for_ready(
        self,
        *,
        provider_instance_id: str,
        timeout: int = 900,
        poll_interval: float = 5.0,
        provider_credential_name: Optional[str] = None,
        region: Optional[str] = None,
    ) -> str:
        # Symmetric with AWS path: poll compute_inventory by bootstrap_id.
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
        return "timeout"

    async def deprovision_node(
        self,
        *,
        provider_instance_id: str,
        provider_credential_name: Optional[str] = None,
    ) -> None:
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
        # GCP machine type discovery deferred to a follow-up.
        return []

    async def get_logs(
        self, *, provider_instance_id: str, provider_credential_name: Optional[str] = None,
    ) -> dict:
        return {"logs": []}

    async def get_log_streaming_info(self, **_kwargs) -> dict:
        return {"supported": False, "reason": "Pulumi GCP adapter uses worker WS for live logs"}

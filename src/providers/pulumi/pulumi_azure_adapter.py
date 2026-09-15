"""Pulumi-backed Azure provisioning adapter.

Symmetric with PulumiGCPAdapter for Google Cloud and PulumiAWSAdapter
for AWS. wait_for_ready / deprovision_node / discover_resources /
get_logs follow the same shape; live AWS-only flows are deferred for
Azure in this iteration.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Optional
from uuid import UUID

import pulumi.automation

from orchestration.config import settings
from providers.aws.bootstrap_builder import (
    build_user_data,
)
from providers.pulumi.base import (
    PulumiProvisioningBase,
)
from providers.pulumi.credentials import (
    resolve_azure_env,
)
from providers.pulumi.programs import (
    build_azure_vm_program,
)
from providers.pulumi.pulumi_aws_adapter import (
    load_providers_config,
)
from orchestration.provisioning.engine.base import (
    AdapterType,
    PricingModel,
    ProviderAdapter,
    ProviderCapabilities,
)
from orchestration.workers.worker_controller.auth import (
    mint_bootstrap_token,
)

logger = logging.getLogger(__name__)

PROJECT_NAME = "inferia-azure"


class PulumiAzureAdapter(PulumiProvisioningBase, ProviderAdapter):
    ADAPTER_TYPE = AdapterType.CLOUD
    CAPABILITIES = ProviderCapabilities(
        supports_multi_gpu=True,
        supports_cluster_mode=True,
        pricing_model=PricingModel.ON_DEMAND,
        features={"cloud": "azure", "bootstrap": "custom-data", "iac": "pulumi"},
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
        cfg = await load_providers_config()
        env_vars = resolve_azure_env(cfg)

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

        location = region or cfg.cloud.azure.region or "eastus"
        program = build_azure_vm_program(
            pool_id=pool_id,
            org_id=org_id,
            bootstrap_id=str(bootstrap_id),
            vm_size=provider_resource_id,
            location=location,
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
        stack.set_config("azure-native:location", pulumi.automation.ConfigValue(location))

        asyncio.create_task(self._provision_async(stack, pool_id))
        return {
            "provider": "azure",
            "provider_instance_id": None,
            "region": location,
            "lifecycle_state": "provisioning",
            "metadata": {
                "pulumi_stack": self.stack_name_for_pool(pool_id),
                "bootstrap_id": str(bootstrap_id),
            },
        }

    async def _provision_async(self, stack, pool_id: str) -> None:
        try:
            await asyncio.to_thread(stack.up)
            logger.info("Pulumi up succeeded for Azure pool %s", pool_id)
        except Exception as e:
            logger.error("Pulumi up failed for Azure pool %s: %s", pool_id, e)
            try:
                await asyncio.to_thread(stack.destroy)
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
        cfg = await load_providers_config()
        env_vars = resolve_azure_env(cfg)
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
        await asyncio.to_thread(stack.destroy)

    async def discover_resources(self, *, region: str = "eastus") -> list:
        # Azure VM SKU discovery deferred to a follow-up.
        return []

    async def get_logs(
        self, *, provider_instance_id: str, provider_credential_name: Optional[str] = None,
    ) -> dict:
        return {"logs": []}

    async def get_log_streaming_info(self, **_kwargs) -> dict:
        return {"supported": False, "reason": "Pulumi Azure adapter uses worker WS for live logs"}

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


def load_providers_config() -> ProvidersConfig:
    """Load the current ProvidersConfig from system_settings.

    Indirection so tests can patch it. Production loads the
    Fernet-decrypted config from config_manager.
    """
    from inferia.services.api_gateway.management.config_manager import config_manager
    data = (config_manager.get_cached() or {}).get("providers") or {}
    return ProvidersConfig.model_validate(data)


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
        image_tag = (
            pool_meta.get("worker_image_tag")
            or account.worker_image_tag
            or settings.worker_image_tag
        )

        if not ami_id:
            try:
                ami_id = latest_dlami_ami(region)
            except AMILookupError as e:
                raise ProvisionError(f"AMI lookup failed: {e}") from e

        self.ensure_state_dir()  # raises PulumiStateError

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
            image_tag=image_tag,
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

        asyncio.create_task(self._provision_async(stack, pool_id, str(bootstrap_id)))

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

    async def _provision_async(self, stack: Any, pool_id: str, bootstrap_id: str) -> None:
        """Run pulumi up. Failure-path/DB-update logic is added in P6."""
        await stack.up_async()
        logger.info("Pulumi up completed for pool %s", pool_id)

    # ------------------------------------------------------------------
    # Stubs for abstract methods — full implementations land in P6.
    # ------------------------------------------------------------------

    async def discover_resources(self) -> List[Dict]:
        raise NotImplementedError("discover_resources not yet implemented for PulumiAWSAdapter")

    async def wait_for_ready(
        self,
        *,
        provider_instance_id: str,
        timeout: int = 300,
        provider_credential_name: Optional[str] = None,
    ) -> str:
        raise NotImplementedError("wait_for_ready not yet implemented for PulumiAWSAdapter")

    async def deprovision_node(
        self,
        *,
        provider_instance_id: str,
        provider_credential_name: Optional[str] = None,
    ) -> None:
        raise NotImplementedError("deprovision_node not yet implemented for PulumiAWSAdapter")

    async def get_logs(
        self,
        *,
        provider_instance_id: str,
        provider_credential_name: Optional[str] = None,
    ) -> Dict:
        raise NotImplementedError("get_logs not yet implemented for PulumiAWSAdapter")

    async def get_log_streaming_info(
        self,
        *,
        provider_instance_id: str,
        provider_credential_name: Optional[str] = None,
    ) -> Dict:
        raise NotImplementedError("get_log_streaming_info not yet implemented for PulumiAWSAdapter")

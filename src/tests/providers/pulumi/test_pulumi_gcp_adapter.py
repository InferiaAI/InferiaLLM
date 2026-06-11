"""Tests for PulumiGCPAdapter — happy provision + creds gating."""
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest

from providers.pulumi.credentials import (
    MissingCredentialsError,
)
from providers.pulumi.pulumi_gcp_adapter import (
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
    from services.api_gateway.config import (
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
    fake_stack.up = MagicMock(return_value=MagicMock(outputs={}))
    fake_stack.set_config = MagicMock()
    pool_id = "00000000-0000-0000-0000-000000000001"
    with patch(
        "providers.pulumi.pulumi_gcp_adapter."
        "pulumi.automation.create_or_select_stack",
        return_value=fake_stack,
    ), patch(
        "providers.pulumi.pulumi_gcp_adapter."
        "load_providers_config",
        return_value=gcp_config,
    ), patch(
        "providers.pulumi.pulumi_gcp_adapter."
        "mint_bootstrap_token",
        new=AsyncMock(return_value=("tok-x", UUID(int=42))),
    ):
        adapter = PulumiGCPAdapter(db=fake_db, state_dir=str(tmp_path))
        result = await adapter.provision_node(
            provider_resource_id="n1-standard-4",
            pool_id=pool_id,
            org_id="o1",
            region="us-central1",
        )
    assert result["provider"] == "gcp"
    assert result["lifecycle_state"] == "provisioning"
    # Clean up background task
    import asyncio
    for t in list(asyncio.all_tasks()):
        if t.get_coro() and t.get_coro().__name__ == "_provision_async":
            t.cancel()
            try:
                await t
            except BaseException:
                pass


@pytest.mark.asyncio
async def test_provision_node_missing_creds(fake_db, tmp_path):
    from services.api_gateway.config import (
        CloudConfig, GCPConfig, ProvidersConfig,
    )
    empty = ProvidersConfig(cloud=CloudConfig(gcp=GCPConfig()))
    with patch(
        "providers.pulumi.pulumi_gcp_adapter."
        "load_providers_config",
        return_value=empty,
    ):
        adapter = PulumiGCPAdapter(db=fake_db, state_dir=str(tmp_path))
        with pytest.raises(MissingCredentialsError):
            await adapter.provision_node(
                provider_resource_id="n1-standard-4",
                pool_id="00000000-0000-0000-0000-000000000001",
                org_id="o1",
                region="us-central1",
            )

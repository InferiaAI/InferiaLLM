"""Tests for PulumiAzureAdapter — happy provision + creds gating."""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest

from providers.pulumi.credentials import (
    MissingCredentialsError,
)
from providers.pulumi.pulumi_azure_adapter import (
    PulumiAzureAdapter,
)


@pytest.fixture
def fake_db():
    db = MagicMock()
    db.execute = AsyncMock(return_value="OK")
    db.fetchrow = AsyncMock(return_value=None)
    return db


@pytest.fixture
def azure_config():
    from services.api_gateway.config import (
        AzureConfig, CloudConfig, ProvidersConfig,
    )
    return ProvidersConfig(cloud=CloudConfig(
        azure=AzureConfig(
            subscription_id="sub-1",
            tenant_id="tenant-1",
            client_id="client-1",
            client_secret="real-secret-1234567",
            region="eastus",
        )
    ))


@pytest.mark.asyncio
async def test_provision_node_kicks_off_async_task(fake_db, azure_config, tmp_path):
    fake_stack = MagicMock()
    fake_stack.up = MagicMock(return_value=MagicMock(outputs={}))
    fake_stack.set_config = MagicMock()
    pool_id = "00000000-0000-0000-0000-000000000001"
    with patch(
        "providers.pulumi.pulumi_azure_adapter."
        "pulumi.automation.create_or_select_stack",
        return_value=fake_stack,
    ), patch(
        "providers.pulumi.pulumi_azure_adapter."
        "load_providers_config",
        return_value=azure_config,
    ), patch(
        "providers.pulumi.pulumi_azure_adapter."
        "mint_bootstrap_token",
        new=AsyncMock(return_value=("tok-x", UUID(int=42))),
    ):
        adapter = PulumiAzureAdapter(db=fake_db, state_dir=str(tmp_path))
        result = await adapter.provision_node(
            provider_resource_id="Standard_NC6s_v3",
            pool_id=pool_id,
            org_id="o1",
            region="eastus",
        )
    assert result["provider"] == "azure"
    assert result["lifecycle_state"] == "provisioning"
    # Clean up background task
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
        AzureConfig, CloudConfig, ProvidersConfig,
    )
    empty = ProvidersConfig(cloud=CloudConfig(azure=AzureConfig()))
    with patch(
        "providers.pulumi.pulumi_azure_adapter."
        "load_providers_config",
        return_value=empty,
    ):
        adapter = PulumiAzureAdapter(db=fake_db, state_dir=str(tmp_path))
        with pytest.raises(MissingCredentialsError):
            await adapter.provision_node(
                provider_resource_id="Standard_NC6s_v3",
                pool_id="00000000-0000-0000-0000-000000000001",
                org_id="o1",
                region="eastus",
            )

"""Tests for GET /management/config/providers/huggingface/token-names.

Verifies:
- Active token names are returned (no token values).
- Inactive tokens are excluded.
- Empty list is handled gracefully.
- Route returns 200 with a deployer-scoped permission (deployment:list).
- An unauthenticated request returns 401.
"""
import pytest
import pytest_asyncio
from unittest.mock import patch

from inferia.services.api_gateway.config import HuggingFaceConfig, HFTokenEntry


_ENDPOINT = "/management/config/providers/huggingface/token-names"


@pytest.mark.asyncio
async def test_token_names_returns_active_names(client, admin_token):
    """Active token names are returned; token values are NOT exposed."""
    hf = HuggingFaceConfig(
        token="hf_legacy",
        tokens=[
            HFTokenEntry(name="prod", token="hf_prod_secret", is_active=True),
            HFTokenEntry(name="staging", token="hf_staging_secret", is_active=True),
        ],
    )
    with patch(
        "inferia.services.api_gateway.management.configuration.settings"
    ) as mock_settings:
        mock_settings.providers.huggingface = hf
        resp = await client.get(
            _ENDPOINT, headers={"Authorization": f"Bearer {admin_token}"}
        )

    assert resp.status_code == 200
    body = resp.json()
    assert set(body["names"]) == {"prod", "staging"}
    # No token values in the response
    assert "hf_prod_secret" not in str(body)
    assert "hf_staging_secret" not in str(body)


@pytest.mark.asyncio
async def test_token_names_excludes_inactive(client, admin_token):
    """Inactive tokens must not appear in the names list."""
    hf = HuggingFaceConfig(
        tokens=[
            HFTokenEntry(name="active", token="hf_act", is_active=True),
            HFTokenEntry(name="inactive", token="hf_inact", is_active=False),
        ]
    )
    with patch(
        "inferia.services.api_gateway.management.configuration.settings"
    ) as mock_settings:
        mock_settings.providers.huggingface = hf
        resp = await client.get(
            _ENDPOINT, headers={"Authorization": f"Bearer {admin_token}"}
        )

    assert resp.status_code == 200
    assert resp.json()["names"] == ["active"]


@pytest.mark.asyncio
async def test_token_names_empty_list(client, admin_token):
    """No tokens configured → empty names list, not an error."""
    hf = HuggingFaceConfig(tokens=[])
    with patch(
        "inferia.services.api_gateway.management.configuration.settings"
    ) as mock_settings:
        mock_settings.providers.huggingface = hf
        resp = await client.get(
            _ENDPOINT, headers={"Authorization": f"Bearer {admin_token}"}
        )

    assert resp.status_code == 200
    assert resp.json()["names"] == []


@pytest.mark.asyncio
async def test_token_names_unauthenticated_returns_401(client):
    """Request without a bearer token must be rejected with 401."""
    resp = await client.get(_ENDPOINT)
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_token_names_deployer_scoped_permission(client, developer_token):
    """A developer (power_user) token can call this endpoint.

    The middleware mock in conftest grants admin:all to every mock role, so
    the developer_token already has deployment:list in this test harness.
    This verifies the route is accessible with a non-admin token type.
    """
    hf = HuggingFaceConfig(
        tokens=[HFTokenEntry(name="alpha", token="hf_alpha", is_active=True)]
    )
    with patch(
        "inferia.services.api_gateway.management.configuration.settings"
    ) as mock_settings:
        mock_settings.providers.huggingface = hf
        resp = await client.get(
            _ENDPOINT, headers={"Authorization": f"Bearer {developer_token}"}
        )

    assert resp.status_code == 200
    assert "alpha" in resp.json()["names"]


@pytest.mark.asyncio
async def test_token_names_skips_blank_names(client, admin_token):
    """Entries with an empty name string are skipped (guard against blank slots)."""
    hf = HuggingFaceConfig(
        tokens=[
            HFTokenEntry(name="real", token="hf_r", is_active=True),
            HFTokenEntry(name="", token="hf_blank_name", is_active=True),
        ]
    )
    with patch(
        "inferia.services.api_gateway.management.configuration.settings"
    ) as mock_settings:
        mock_settings.providers.huggingface = hf
        resp = await client.get(
            _ENDPOINT, headers={"Authorization": f"Bearer {admin_token}"}
        )

    assert resp.status_code == 200
    assert resp.json()["names"] == ["real"]


@pytest.mark.asyncio
async def test_token_names_only_inactive_tokens_returns_empty(client, admin_token):
    """When all tokens are inactive the list is empty."""
    hf = HuggingFaceConfig(
        tokens=[
            HFTokenEntry(name="old", token="hf_old", is_active=False),
        ]
    )
    with patch(
        "inferia.services.api_gateway.management.configuration.settings"
    ) as mock_settings:
        mock_settings.providers.huggingface = hf
        resp = await client.get(
            _ENDPOINT, headers={"Authorization": f"Bearer {admin_token}"}
        )

    assert resp.status_code == 200
    assert resp.json()["names"] == []

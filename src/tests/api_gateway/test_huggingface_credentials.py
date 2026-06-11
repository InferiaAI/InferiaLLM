"""Tests for the universal provider-credential endpoints — huggingface branch.

POST/GET/PUT/DELETE ``/management/config/providers/huggingface/credentials``
map to the ``huggingface.tokens`` list — the same storage that the deploy
form's ``token-names`` endpoint and ``resolve_hf_token`` read. This mirrors
the Nosana ``api_keys`` behaviour so the dashboard can manage HF tokens with
the same add/list/delete UX.

Verifies:
- Add appends ``{name, token, is_active}`` to ``huggingface.tokens``.
- Duplicate names are rejected (400) and nothing is persisted.
- List returns name/type/is_active only — never the token value.
- Update toggles ``is_active`` / rotates the token value by name.
- Update/delete of an unknown name returns 404 and persists nothing.
- Delete removes only the named entry.
- The unsupported-provider error message advertises ``huggingface``.
- The stored shape matches the ``token-names`` / resolver contract.
"""
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from api_gateway.config import (
    HFTokenEntry,
    HuggingFaceConfig,
    ProvidersConfig,
)

_BASE = "/management/config/providers/huggingface/credentials"


@contextmanager
def _patched(tokens):
    """Patch ``configuration.settings`` with a real ``ProvidersConfig`` (so
    ``model_dump()`` works) seeded with ``tokens``, and mock persistence/audit
    so no DB is required. Yields the ``save_config`` and ``_force_replace_config``
    mocks for assertions.
    """
    providers = ProvidersConfig(huggingface=HuggingFaceConfig(tokens=list(tokens)))
    save = AsyncMock()
    force_replace = AsyncMock()
    with (
        patch(
            "api_gateway.management.configuration.settings",
            MagicMock(providers=providers),
        ),
        patch(
            "api_gateway.management.config_manager.config_manager.save_config",
            save,
        ),
        patch(
            "api_gateway.management.config_manager.config_manager._force_replace_config",
            force_replace,
        ),
        patch(
            "api_gateway.management.configuration.audit_service.log_event",
            AsyncMock(),
        ),
        patch(
            "api_gateway.management.configuration._cleanup_provider_resources",
            AsyncMock(),
        ),
    ):
        yield save, force_replace


def _saved_tokens(persist_mock):
    """Extract ``huggingface.tokens`` from the (positional) config arg of the
    last save_config / _force_replace_config call."""
    db_config = persist_mock.call_args.args[1]
    return db_config["providers"]["huggingface"]["tokens"]


@pytest.mark.asyncio
async def test_add_hf_token(client, admin_token):
    with _patched([]) as (save, _):
        resp = await client.post(
            _BASE,
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"name": "prod", "credential_type": "token", "value": "hf_prod_xyz"},
        )
    assert resp.status_code == 200
    assert resp.json()["name"] == "prod"
    save.assert_awaited_once()
    toks = _saved_tokens(save)
    assert any(
        t["name"] == "prod" and t["token"] == "hf_prod_xyz" and t["is_active"] is True
        for t in toks
    )


@pytest.mark.asyncio
async def test_add_hf_token_appends_to_existing(client, admin_token):
    with _patched([HFTokenEntry(name="prod", token="hf_p", is_active=True)]) as (save, _):
        resp = await client.post(
            _BASE,
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"name": "staging", "credential_type": "token", "value": "hf_s"},
        )
    assert resp.status_code == 200
    names = {t["name"] for t in _saved_tokens(save)}
    assert names == {"prod", "staging"}


@pytest.mark.asyncio
async def test_add_hf_token_duplicate_name_400(client, admin_token):
    with _patched([HFTokenEntry(name="dup", token="hf_old", is_active=True)]) as (save, _):
        resp = await client.post(
            _BASE,
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"name": "dup", "credential_type": "token", "value": "hf_new"},
        )
    assert resp.status_code == 400
    assert "already exists" in resp.json()["detail"]
    save.assert_not_awaited()


@pytest.mark.asyncio
async def test_list_hf_credentials_masked(client, admin_token):
    tokens = [
        HFTokenEntry(name="prod", token="hf_prod_secret", is_active=True),
        HFTokenEntry(name="old", token="hf_old_secret", is_active=False),
    ]
    with _patched(tokens):
        resp = await client.get(_BASE, headers={"Authorization": f"Bearer {admin_token}"})
    assert resp.status_code == 200
    creds = resp.json()["credentials"]
    by_name = {c["name"]: c for c in creds}
    assert set(by_name) == {"prod", "old"}
    assert by_name["prod"]["credential_type"] == "token"
    assert by_name["prod"]["is_active"] is True
    assert by_name["old"]["is_active"] is False
    # Token values must never leave the server.
    assert "hf_prod_secret" not in str(creds)
    assert "hf_old_secret" not in str(creds)


@pytest.mark.asyncio
async def test_list_hf_credentials_skips_blank_names(client, admin_token):
    tokens = [
        HFTokenEntry(name="real", token="hf_r", is_active=True),
        HFTokenEntry(name="", token="hf_blank", is_active=True),
    ]
    with _patched(tokens):
        resp = await client.get(_BASE, headers={"Authorization": f"Bearer {admin_token}"})
    assert resp.status_code == 200
    names = [c["name"] for c in resp.json()["credentials"]]
    assert names == ["real"]


@pytest.mark.asyncio
async def test_update_hf_token_is_active(client, admin_token):
    with _patched([HFTokenEntry(name="prod", token="hf_x", is_active=True)]) as (save, _):
        resp = await client.put(
            f"{_BASE}/prod",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"is_active": False},
        )
    assert resp.status_code == 200
    save.assert_awaited_once()
    toks = _saved_tokens(save)
    assert toks[0]["name"] == "prod" and toks[0]["is_active"] is False
    # value untouched when only is_active is sent
    assert toks[0]["token"] == "hf_x"


@pytest.mark.asyncio
async def test_update_hf_token_value(client, admin_token):
    with _patched([HFTokenEntry(name="prod", token="hf_x", is_active=True)]) as (save, _):
        resp = await client.put(
            f"{_BASE}/prod",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"value": "hf_new_secret"},
        )
    assert resp.status_code == 200
    assert _saved_tokens(save)[0]["token"] == "hf_new_secret"


@pytest.mark.asyncio
async def test_update_hf_token_not_found_404(client, admin_token):
    with _patched([HFTokenEntry(name="prod", token="hf_x")]) as (save, _):
        resp = await client.put(
            f"{_BASE}/ghost",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"is_active": False},
        )
    assert resp.status_code == 404
    save.assert_not_awaited()


@pytest.mark.asyncio
async def test_delete_hf_token(client, admin_token):
    seeded = [
        HFTokenEntry(name="prod", token="hf_p"),
        HFTokenEntry(name="staging", token="hf_s"),
    ]
    with _patched(seeded) as (_, force_replace):
        resp = await client.delete(
            f"{_BASE}/prod", headers={"Authorization": f"Bearer {admin_token}"}
        )
    assert resp.status_code == 200
    force_replace.assert_awaited_once()
    names = {t["name"] for t in _saved_tokens(force_replace)}
    assert names == {"staging"}


@pytest.mark.asyncio
async def test_delete_hf_token_not_found_404(client, admin_token):
    with _patched([HFTokenEntry(name="prod", token="hf_p")]) as (_, force_replace):
        resp = await client.delete(
            f"{_BASE}/ghost", headers={"Authorization": f"Bearer {admin_token}"}
        )
    assert resp.status_code == 404
    force_replace.assert_not_awaited()


@pytest.mark.asyncio
async def test_add_unsupported_provider_lists_huggingface(client, admin_token):
    """The unsupported-provider error advertises huggingface as valid."""
    with _patched([]):
        resp = await client.post(
            "/management/config/providers/unknownprov/credentials",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"name": "x", "credential_type": "token", "value": "v"},
        )
    assert resp.status_code == 400
    assert "huggingface" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_added_token_shape_matches_token_names_contract(client, admin_token):
    """A token added via the credential endpoint is stored as
    ``{name, token, is_active}`` under ``huggingface.tokens`` — exactly the
    shape ``list_hf_token_names`` and ``resolve_hf_token`` consume."""
    with _patched([]) as (save, _):
        resp = await client.post(
            _BASE,
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"name": "deployer", "credential_type": "token", "value": "hf_deploy"},
        )
    assert resp.status_code == 200
    entry = _saved_tokens(save)[0]
    assert {"name", "token", "is_active"} <= set(entry)
    # Re-hydrate into the model the endpoints read → token-names sees it active.
    hf = HuggingFaceConfig(tokens=[HFTokenEntry(**entry)])
    active = [t.name for t in hf.tokens if t.is_active and t.name]
    assert active == ["deployer"]


@pytest.mark.asyncio
async def test_credential_endpoints_require_auth(client):
    """All huggingface credential routes reject unauthenticated requests."""
    assert (await client.get(_BASE)).status_code == 401
    assert (
        await client.post(
            _BASE, json={"name": "x", "credential_type": "token", "value": "v"}
        )
    ).status_code == 401
    assert (await client.delete(f"{_BASE}/x")).status_code == 401

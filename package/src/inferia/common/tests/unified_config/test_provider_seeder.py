"""Tests for provider_seeder: pure-function paths + DB integration (skip-gated).

Pure-function tests run unconditionally and aim for ≥95 % coverage on
provider_seeder.py without touching a database.

DB integration tests are skip-gated on INFERIA_TEST_DATABASE_URL.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any
from unittest.mock import MagicMock

import pytest

from inferia.common.unified_config.provider_seeder import (
    ProviderRow,
    SeedReport,
    _extract_rows,
    _load_config,
    _make_fernet,
    _encrypt,
    _nonempty,
    _maybe_row,
    seed_providers_from_yaml,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_providers(**kwargs):
    """Create a simple namespace-like object with model_dump that wraps kwargs."""

    class _FakeProviders:
        def model_dump(self):
            return kwargs

    return _FakeProviders()


def _providers_from_dict(d: dict):
    """Return an object with model_dump() that returns *d*."""
    return _make_providers(**d)


VALID_FERNET_KEY = "7or2-zE2nIBuJENMcpZR7dyLjNRgbdi5EgalrGCQBlI="


# ---------------------------------------------------------------------------
# _nonempty
# ---------------------------------------------------------------------------

class TestNonempty:
    def test_truthy_string(self):
        assert _nonempty("hello") is True

    def test_whitespace_only(self):
        assert _nonempty("   ") is False

    def test_empty_string(self):
        assert _nonempty("") is False

    def test_none(self):
        assert _nonempty(None) is False

    def test_integer(self):
        assert _nonempty(42) is False

    def test_list(self):
        assert _nonempty([]) is False


# ---------------------------------------------------------------------------
# _maybe_row
# ---------------------------------------------------------------------------

class TestMaybeRow:
    def test_appends_when_nonempty(self):
        rows: list[ProviderRow] = []
        _maybe_row(rows, {"key": "val"}, "key", "p", "n", "ct")
        assert len(rows) == 1
        r = rows[0]
        assert r.provider == "p"
        assert r.name == "n"
        assert r.credential_type == "ct"
        assert r.value == "val"
        assert r.is_active is True

    def test_skips_empty_value(self):
        rows: list[ProviderRow] = []
        _maybe_row(rows, {"key": ""}, "key", "p", "n", "ct")
        assert rows == []

    def test_skips_missing_key(self):
        rows: list[ProviderRow] = []
        _maybe_row(rows, {}, "key", "p", "n", "ct")
        assert rows == []

    def test_skips_none_value(self):
        rows: list[ProviderRow] = []
        _maybe_row(rows, {"key": None}, "key", "p", "n", "ct")
        assert rows == []

    def test_strips_whitespace(self):
        rows: list[ProviderRow] = []
        _maybe_row(rows, {"key": "  secret  "}, "key", "p", "n", "ct")
        assert rows[0].value == "secret"


# ---------------------------------------------------------------------------
# _extract_rows — empty / missing providers
# ---------------------------------------------------------------------------

class TestExtractRowsEmpty:
    def test_none_input(self):
        assert _extract_rows(None) == []

    def test_empty_dict(self):
        assert _extract_rows(_providers_from_dict({})) == []

    def test_providers_all_empty_strings(self):
        p = _providers_from_dict(
            {
                "aws": {"access_key_id": "", "secret_access_key": ""},
                "gcp": {"project_id": "", "service_account_json": ""},
                "azure": {"subscription_id": "", "tenant_id": "", "client_id": "", "client_secret": ""},
                "ibm": {"api_key": "", "resource_group_id": ""},
                "nosana": {"wallet_private_key": "", "api_keys": []},
            }
        )
        assert _extract_rows(p) == []

    def test_providers_all_none_values(self):
        p = _providers_from_dict(
            {
                "aws": {"access_key_id": None, "secret_access_key": None},
                "gcp": {"project_id": None},
                "azure": {"client_secret": None},
                "ibm": {"api_key": None},
                "nosana": {"wallet_private_key": None},
            }
        )
        assert _extract_rows(p) == []

    def test_raw_dict_input(self):
        """_extract_rows also accepts a plain dict (no model_dump)."""
        result = _extract_rows({})
        assert result == []

    def test_unknown_type_returns_empty(self):
        """_extract_rows returns [] for any non-model, non-dict input."""
        assert _extract_rows("not-a-dict") == []
        assert _extract_rows(42) == []
        assert _extract_rows([]) == []


# ---------------------------------------------------------------------------
# _extract_rows — aws
# ---------------------------------------------------------------------------

class TestExtractRowsAws:
    def test_access_key_id(self):
        p = _providers_from_dict({"aws": {"access_key_id": "AKIAIOSFODNN"}})
        rows = _extract_rows(p)
        assert len(rows) == 1
        r = rows[0]
        assert r.provider == "aws"
        assert r.name == "default"
        assert r.credential_type == "access_key_id"
        assert r.value == "AKIAIOSFODNN"

    def test_secret_access_key(self):
        p = _providers_from_dict(
            {"aws": {"secret_access_key": "wJalrXUtnFEMI"}}
        )
        rows = _extract_rows(p)
        assert len(rows) == 1
        assert rows[0].credential_type == "secret_access_key"

    def test_both_aws_fields(self):
        p = _providers_from_dict(
            {
                "aws": {
                    "access_key_id": "AKIAIOSFODNN",
                    "secret_access_key": "secret",
                }
            }
        )
        rows = _extract_rows(p)
        assert len(rows) == 2
        types = {r.credential_type for r in rows}
        assert types == {"access_key_id", "secret_access_key"}

    def test_region_not_seeded(self):
        """Region is config, not a credential — must not appear as a row."""
        p = _providers_from_dict({"aws": {"access_key_id": "AKIA", "region": "us-east-1"}})
        rows = _extract_rows(p)
        cred_types = {r.credential_type for r in rows}
        assert "region" not in cred_types


# ---------------------------------------------------------------------------
# _extract_rows — gcp
# ---------------------------------------------------------------------------

class TestExtractRowsGcp:
    def test_service_account_json(self):
        p = _providers_from_dict(
            {"gcp": {"service_account_json": '{"type":"service_account"}'}}
        )
        rows = _extract_rows(p)
        assert len(rows) == 1
        assert rows[0].provider == "gcp"
        assert rows[0].credential_type == "service_account_json"

    def test_project_id(self):
        p = _providers_from_dict({"gcp": {"project_id": "my-gcp-project"}})
        rows = _extract_rows(p)
        assert len(rows) == 1
        assert rows[0].credential_type == "project_id"

    def test_both_gcp_fields(self):
        p = _providers_from_dict(
            {
                "gcp": {
                    "project_id": "proj-123",
                    "service_account_json": '{"type":"service_account"}',
                }
            }
        )
        rows = _extract_rows(p)
        assert len(rows) == 2
        types = {r.credential_type for r in rows}
        assert types == {"project_id", "service_account_json"}

    def test_region_not_seeded(self):
        p = _providers_from_dict({"gcp": {"project_id": "p", "region": "us-central1"}})
        rows = _extract_rows(p)
        cred_types = {r.credential_type for r in rows}
        assert "region" not in cred_types


# ---------------------------------------------------------------------------
# _extract_rows — azure
# ---------------------------------------------------------------------------

class TestExtractRowsAzure:
    def test_subscription_id(self):
        p = _providers_from_dict({"azure": {"subscription_id": "sub-abc-123"}})
        rows = _extract_rows(p)
        assert len(rows) == 1
        assert rows[0].provider == "azure"
        assert rows[0].credential_type == "subscription_id"
        assert rows[0].name == "default"

    def test_tenant_id(self):
        p = _providers_from_dict({"azure": {"tenant_id": "tenant-xyz"}})
        rows = _extract_rows(p)
        assert len(rows) == 1
        assert rows[0].credential_type == "tenant_id"

    def test_client_id(self):
        p = _providers_from_dict({"azure": {"client_id": "client-id-here"}})
        rows = _extract_rows(p)
        assert len(rows) == 1
        assert rows[0].credential_type == "client_id"

    def test_client_secret(self):
        p = _providers_from_dict({"azure": {"client_secret": "my-client-secret"}})
        rows = _extract_rows(p)
        assert len(rows) == 1
        assert rows[0].credential_type == "client_secret"

    def test_all_azure_fields(self):
        p = _providers_from_dict(
            {
                "azure": {
                    "subscription_id": "sub",
                    "tenant_id": "ten",
                    "client_id": "cid",
                    "client_secret": "csec",
                }
            }
        )
        rows = _extract_rows(p)
        assert len(rows) == 4
        types = {r.credential_type for r in rows}
        assert types == {"subscription_id", "tenant_id", "client_id", "client_secret"}

    def test_partial_azure(self):
        p = _providers_from_dict(
            {"azure": {"subscription_id": "sub", "tenant_id": ""}}
        )
        rows = _extract_rows(p)
        assert len(rows) == 1
        assert rows[0].credential_type == "subscription_id"

    def test_region_not_seeded(self):
        p = _providers_from_dict({"azure": {"subscription_id": "s", "region": "eastus"}})
        rows = _extract_rows(p)
        cred_types = {r.credential_type for r in rows}
        assert "region" not in cred_types


# ---------------------------------------------------------------------------
# _extract_rows — ibm
# ---------------------------------------------------------------------------

class TestExtractRowsIbm:
    def test_api_key(self):
        p = _providers_from_dict({"ibm": {"api_key": "ibm-api-key-value"}})
        rows = _extract_rows(p)
        assert len(rows) == 1
        assert rows[0].provider == "ibm"
        assert rows[0].credential_type == "api_key"
        assert rows[0].name == "default"

    def test_resource_group_id(self):
        p = _providers_from_dict({"ibm": {"resource_group_id": "rg-123"}})
        rows = _extract_rows(p)
        assert len(rows) == 1
        assert rows[0].credential_type == "resource_group_id"

    def test_both_ibm_fields(self):
        p = _providers_from_dict(
            {"ibm": {"api_key": "key", "resource_group_id": "rg"}}
        )
        rows = _extract_rows(p)
        assert len(rows) == 2
        types = {r.credential_type for r in rows}
        assert types == {"api_key", "resource_group_id"}

    def test_region_not_seeded(self):
        p = _providers_from_dict({"ibm": {"api_key": "k", "region": "us-south"}})
        rows = _extract_rows(p)
        cred_types = {r.credential_type for r in rows}
        assert "region" not in cred_types

    def test_empty_ibm_skipped(self):
        p = _providers_from_dict({"ibm": {"api_key": "", "resource_group_id": ""}})
        assert _extract_rows(p) == []


# ---------------------------------------------------------------------------
# _extract_rows — nosana (wallet + api_keys list)
# ---------------------------------------------------------------------------

class TestExtractRowsNosana:
    def test_wallet_private_key(self):
        p = _providers_from_dict(
            {"nosana": {"wallet_private_key": "abc123"}}
        )
        rows = _extract_rows(p)
        assert len(rows) == 1
        assert rows[0].provider == "nosana"
        assert rows[0].name == "wallet"
        assert rows[0].credential_type == "wallet_private_key"

    def test_single_api_key(self):
        p = _providers_from_dict(
            {
                "nosana": {
                    "api_keys": [{"name": "prod", "key": "prod-key"}]
                }
            }
        )
        rows = _extract_rows(p)
        assert len(rows) == 1
        assert rows[0].name == "prod"
        assert rows[0].credential_type == "api_key"
        assert rows[0].value == "prod-key"

    def test_multiple_api_keys(self):
        p = _providers_from_dict(
            {
                "nosana": {
                    "api_keys": [
                        {"name": "prod", "key": "prod-key"},
                        {"name": "staging", "key": "staging-key"},
                        {"name": "dev", "key": "dev-key"},
                    ]
                }
            }
        )
        rows = _extract_rows(p)
        assert len(rows) == 3
        names = [r.name for r in rows]
        assert "prod" in names
        assert "staging" in names
        assert "dev" in names

    def test_api_key_is_active_false(self):
        p = _providers_from_dict(
            {
                "nosana": {
                    "api_keys": [
                        {"name": "disabled", "key": "some-key", "is_active": False}
                    ]
                }
            }
        )
        rows = _extract_rows(p)
        assert len(rows) == 1
        assert rows[0].is_active is False

    def test_api_key_is_active_defaults_true(self):
        p = _providers_from_dict(
            {
                "nosana": {
                    "api_keys": [{"name": "nostate", "key": "k"}]
                }
            }
        )
        rows = _extract_rows(p)
        assert rows[0].is_active is True

    def test_api_keys_entry_missing_name_skipped(self):
        p = _providers_from_dict(
            {
                "nosana": {
                    "api_keys": [
                        {"key": "orphan-key"},  # no name
                        {"name": "ok", "key": "good-key"},
                    ]
                }
            }
        )
        rows = _extract_rows(p)
        assert len(rows) == 1
        assert rows[0].name == "ok"

    def test_api_keys_entry_missing_key_skipped(self):
        p = _providers_from_dict(
            {
                "nosana": {
                    "api_keys": [
                        {"name": "novalue"},  # no key
                    ]
                }
            }
        )
        rows = _extract_rows(p)
        assert rows == []

    def test_api_keys_entry_empty_key_skipped(self):
        p = _providers_from_dict(
            {
                "nosana": {
                    "api_keys": [{"name": "x", "key": ""}]
                }
            }
        )
        rows = _extract_rows(p)
        assert rows == []

    def test_api_keys_non_dict_entry_skipped(self):
        p = _providers_from_dict(
            {
                "nosana": {
                    "api_keys": ["not-a-dict", {"name": "good", "key": "v"}]
                }
            }
        )
        rows = _extract_rows(p)
        assert len(rows) == 1

    def test_wallet_and_api_keys_combined(self):
        p = _providers_from_dict(
            {
                "nosana": {
                    "wallet_private_key": "wallet-secret",
                    "api_keys": [
                        {"name": "prod", "key": "prod-key"},
                    ],
                }
            }
        )
        rows = _extract_rows(p)
        assert len(rows) == 2
        cred_types = {r.credential_type for r in rows}
        assert cred_types == {"wallet_private_key", "api_key"}


# ---------------------------------------------------------------------------
# _extract_rows — full tree (all 5 providers)
# ---------------------------------------------------------------------------

class TestExtractRowsFullTree:
    def test_all_branches(self):
        p = _providers_from_dict(
            {
                "aws": {
                    "access_key_id": "AKIA",
                    "secret_access_key": "secret",
                },
                "gcp": {
                    "project_id": "proj",
                    "service_account_json": "{}",
                },
                "azure": {
                    "subscription_id": "sub",
                    "tenant_id": "ten",
                    "client_id": "cid",
                    "client_secret": "csec",
                },
                "ibm": {
                    "api_key": "ibmkey",
                    "resource_group_id": "rg",
                },
                "nosana": {
                    "wallet_private_key": "wallet",
                    "api_keys": [
                        {"name": "prod", "key": "pk"},
                        {"name": "staging", "key": "sk"},
                    ],
                },
            }
        )
        rows = _extract_rows(p)
        # 2 (aws) + 2 (gcp) + 4 (azure) + 2 (ibm) + 1 (wallet) + 2 (api_keys) = 13
        assert len(rows) == 13

    def test_providers_not_in_tree_ignored(self):
        """Old providers (akash, chroma, groq, lakera) that no longer exist produce no rows."""
        p = _providers_from_dict(
            {
                "aws": {"access_key_id": "AKIA"},
                # These old keys must be silently ignored
                "depin": {"akash": {"mnemonic": "word1 word2"}},
                "guardrails": {"groq": {"api_key": "groq-key"}},
                "vectordb": {"chroma": {"api_key": "ck-key"}},
            }
        )
        rows = _extract_rows(p)
        # Only aws/access_key_id should produce a row
        assert len(rows) == 1
        assert rows[0].provider == "aws"


# ---------------------------------------------------------------------------
# _make_fernet
# ---------------------------------------------------------------------------

class TestMakeFernet:
    def test_valid_key(self):
        f = _make_fernet(VALID_FERNET_KEY)
        assert f is not None

    def test_invalid_key_returns_none(self):
        f = _make_fernet("not-a-valid-fernet-key")
        assert f is None

    def test_empty_string_returns_none(self):
        f = _make_fernet("")
        assert f is None

    def test_bytes_key_accepted(self):
        from cryptography.fernet import Fernet

        key = Fernet.generate_key()
        f = _make_fernet(key)
        assert f is not None


# ---------------------------------------------------------------------------
# _encrypt
# ---------------------------------------------------------------------------

class TestEncrypt:
    def test_roundtrip(self):
        from cryptography.fernet import Fernet

        key = Fernet.generate_key().decode()
        f = _make_fernet(key)
        ciphertext = _encrypt(f, "my-secret")
        decrypted = f.decrypt(ciphertext.encode()).decode()
        assert decrypted == "my-secret"

    def test_output_differs_from_plaintext(self):
        from cryptography.fernet import Fernet

        key = Fernet.generate_key().decode()
        f = _make_fernet(key)
        ciphertext = _encrypt(f, "plaintext-secret")
        assert ciphertext != "plaintext-secret"


# ---------------------------------------------------------------------------
# seed_providers_from_yaml — pure async unit tests (no real DB)
# ---------------------------------------------------------------------------

class TestSeedProvidersFromYamlUnit:
    """Unit tests that stub out asyncpg entirely."""

    @pytest.mark.asyncio
    async def test_skips_when_no_encryption_key(self):
        report = await seed_providers_from_yaml("postgresql://x", None)
        assert report.skipped is True
        assert "encryption_key" in report.reason

    @pytest.mark.asyncio
    async def test_skips_when_encryption_key_empty_string(self):
        report = await seed_providers_from_yaml("postgresql://x", "")
        assert report.skipped is True

    @pytest.mark.asyncio
    async def test_skips_when_invalid_fernet_key(self):
        report = await seed_providers_from_yaml("postgresql://x", "bad-key")
        assert report.skipped is True
        assert "invalid" in report.reason.lower()

    @pytest.mark.asyncio
    async def test_skips_when_no_yaml_config(self, monkeypatch):
        import inferia.common.unified_config.provider_seeder as _mod
        from cryptography.fernet import Fernet

        key = Fernet.generate_key().decode()
        monkeypatch.setattr(_mod, "_load_config", lambda: None)
        report = await seed_providers_from_yaml("postgresql://x", key)
        assert report.skipped is True
        assert "no yaml" in report.reason.lower()

    @pytest.mark.asyncio
    async def test_skips_when_cfg_has_no_providers(self, monkeypatch):
        import inferia.common.unified_config.provider_seeder as _mod
        from cryptography.fernet import Fernet

        key = Fernet.generate_key().decode()

        class _FakeCfg:
            providers = None

        monkeypatch.setattr(_mod, "_load_config", lambda: _FakeCfg())
        report = await seed_providers_from_yaml("postgresql://x", key)
        assert report.skipped is True
        assert "no providers" in report.reason.lower()

    @pytest.mark.asyncio
    async def test_skips_when_cfg_provided_directly_but_no_providers(self):
        from cryptography.fernet import Fernet

        key = Fernet.generate_key().decode()

        class _FakeCfg:
            providers = None

        report = await seed_providers_from_yaml("postgresql://x", key, cfg=_FakeCfg())
        assert report.skipped is True

    @pytest.mark.asyncio
    async def test_db_insert_update_delete_mocked(self, monkeypatch):
        """Cover the asyncpg transaction block with a mock connection."""
        from cryptography.fernet import Fernet
        from unittest.mock import AsyncMock, MagicMock, patch

        key = Fernet.generate_key().decode()

        # Build a fake cfg with one credential row (flat aws).
        class _FakeProviders:
            def model_dump(self):
                return {"aws": {"access_key_id": "AKIA123"}}

        class _FakeCfg:
            providers = _FakeProviders()

        # Fake asyncpg connection with a context-manager transaction.
        class _FakeTransaction:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

        mock_conn = MagicMock()
        mock_conn.transaction.return_value = _FakeTransaction()
        mock_conn.fetch = AsyncMock(return_value=[])  # no existing rows
        mock_conn.execute = AsyncMock(return_value="INSERT 0 1")
        mock_conn.close = AsyncMock()

        with patch(
            "asyncpg.connect",
            AsyncMock(return_value=mock_conn),
        ):
            report = await seed_providers_from_yaml(
                "postgresql://test/db", key, cfg=_FakeCfg()
            )

        assert report.skipped is False
        assert report.inserted == 1
        assert report.updated == 0
        assert report.deleted == 0

    @pytest.mark.asyncio
    async def test_db_update_and_delete_mocked(self, monkeypatch):
        """Cover updated-row and delete-row paths in the transaction block."""
        from cryptography.fernet import Fernet
        from unittest.mock import AsyncMock, MagicMock, patch

        key = Fernet.generate_key().decode()

        # Two desired rows: aws access_key_id (exists) + secret (new).
        class _FakeProviders:
            def model_dump(self):
                return {
                    "aws": {
                        "access_key_id": "AKIA",
                        "secret_access_key": "sekret",
                    }
                }

        class _FakeCfg:
            providers = _FakeProviders()

        class _FakeTransaction:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

        # Simulate DB has "aws/default" AND an old "gcp/default" row that is no
        # longer in yaml (should be deleted).
        # NOTE: the seeder tracks uniqueness by (provider, name) — both desired
        # rows share (aws, default), so both are counted as "updated".
        existing_rows = [
            {"provider": "aws", "name": "default"},
            {"provider": "gcp", "name": "default"},  # will be deleted
        ]
        mock_conn = MagicMock()
        mock_conn.transaction.return_value = _FakeTransaction()
        mock_conn.fetch = AsyncMock(return_value=existing_rows)
        mock_conn.execute = AsyncMock(return_value="INSERT 0 1")
        mock_conn.close = AsyncMock()

        with patch(
            "asyncpg.connect",
            AsyncMock(return_value=mock_conn),
        ):
            report = await seed_providers_from_yaml(
                "postgresql://test/db", key, cfg=_FakeCfg()
            )

        assert report.skipped is False
        # Both desired rows share (aws, default) which already existed → both updated.
        assert report.updated == 2
        assert report.inserted == 0
        # gcp/default was deleted (not in yaml)
        assert report.deleted == 1


class TestLoadConfig:
    """Coverage for _load_config() helper (lines 220-224)."""

    def test_load_config_returns_none_without_yaml(self, monkeypatch, tmp_path):
        """When no yaml file exists and INFERIA_CONFIG is unset, returns None."""
        monkeypatch.delenv("INFERIA_CONFIG", raising=False)
        monkeypatch.chdir(tmp_path)  # ensure no inferia.yaml in CWD
        from inferia.common.unified_config.loader import _clear_cache
        _clear_cache()
        result = _load_config()
        assert result is None

    def test_load_config_returns_config_with_yaml(self, monkeypatch, tmp_path):
        """When INFERIA_CONFIG points to a valid yaml, returns InferiaConfig."""
        yaml_file = tmp_path / "inferia.yaml"
        yaml_file.write_text("version: 1\n", encoding="utf-8")
        monkeypatch.setenv("INFERIA_CONFIG", str(yaml_file))
        from inferia.common.unified_config.loader import _clear_cache
        _clear_cache()
        result = _load_config()
        assert result is not None
        assert result.version == 1


# ---------------------------------------------------------------------------
# SeedReport dataclass
# ---------------------------------------------------------------------------

class TestSeedReport:
    def test_defaults(self):
        r = SeedReport()
        assert r.skipped is False
        assert r.reason == ""
        assert r.inserted == 0
        assert r.updated == 0
        assert r.deleted == 0

    def test_skipped_with_reason(self):
        r = SeedReport(skipped=True, reason="test")
        assert r.skipped is True
        assert r.reason == "test"


# ---------------------------------------------------------------------------
# DB integration tests (skip-gated on INFERIA_TEST_DATABASE_URL)
# ---------------------------------------------------------------------------

_DB_URL = os.environ.get("INFERIA_TEST_DATABASE_URL")
_DB_SKIP = pytest.mark.skipif(
    not _DB_URL, reason="INFERIA_TEST_DATABASE_URL not set"
)


@dataclass
class _DBFixture:
    dsn: str
    fernet_key: str


@pytest.fixture(scope="module")
def db_fixture():
    if not _DB_URL:
        pytest.skip("INFERIA_TEST_DATABASE_URL not set")
    from cryptography.fernet import Fernet

    key = Fernet.generate_key().decode()
    return _DBFixture(dsn=_DB_URL, fernet_key=key)


@pytest.fixture(autouse=False)
def _clean_provider_credentials(db_fixture):
    """Truncate provider_credentials before and after each DB test."""
    import asyncio
    import asyncpg

    async def _truncate():
        conn = await asyncpg.connect(db_fixture.dsn)
        try:
            await conn.execute("DELETE FROM provider_credentials")
        finally:
            await conn.close()

    asyncio.get_event_loop().run_until_complete(_truncate())
    yield
    asyncio.get_event_loop().run_until_complete(_truncate())


@_DB_SKIP
class TestSeedProvidersDB:
    def _make_cfg(self, providers_dict: dict):
        class _FakeProviders:
            def model_dump(self):
                return providers_dict

        class _FakeCfg:
            providers = _FakeProviders()

        return _FakeCfg()

    def _run(self, coro):
        import asyncio

        return asyncio.get_event_loop().run_until_complete(coro)

    @pytest.mark.asyncio
    async def test_empty_db_inserts_rows(self, db_fixture, _clean_provider_credentials):
        cfg = self._make_cfg(
            {
                "aws": {"access_key_id": "AKIA", "secret_access_key": "S"},
                "ibm": {"api_key": "ibm-key"},
            }
        )
        report = await seed_providers_from_yaml(
            db_fixture.dsn, db_fixture.fernet_key, cfg=cfg
        )
        assert report.skipped is False
        assert report.inserted == 3  # access_key_id + secret_access_key + ibm.api_key
        assert report.updated == 0
        assert report.deleted == 0

    @pytest.mark.asyncio
    async def test_existing_rows_omitted_from_yaml_are_deleted(
        self, db_fixture, _clean_provider_credentials
    ):
        import asyncpg
        from cryptography.fernet import Fernet

        fernet = Fernet(db_fixture.fernet_key.encode())
        conn = await asyncpg.connect(db_fixture.dsn)
        try:
            await conn.execute(
                """INSERT INTO provider_credentials
                   (provider, name, credential_type, credential_value_encrypted)
                   VALUES ('old-provider', 'old-name', 'api_key', $1)""",
                fernet.encrypt(b"old-val").decode(),
            )
        finally:
            await conn.close()

        # Yaml only has ibm; old-provider should be deleted
        cfg = self._make_cfg({"ibm": {"api_key": "ibm-key"}})
        report = await seed_providers_from_yaml(
            db_fixture.dsn, db_fixture.fernet_key, cfg=cfg
        )
        assert report.deleted == 1
        assert report.inserted == 1

    @pytest.mark.asyncio
    async def test_update_existing_row(self, db_fixture, _clean_provider_credentials):
        import asyncpg
        from cryptography.fernet import Fernet

        fernet = Fernet(db_fixture.fernet_key.encode())
        conn = await asyncpg.connect(db_fixture.dsn)
        try:
            await conn.execute(
                """INSERT INTO provider_credentials
                   (provider, name, credential_type, credential_value_encrypted)
                   VALUES ('ibm', 'default', 'api_key', $1)""",
                fernet.encrypt(b"old-key").decode(),
            )
        finally:
            await conn.close()

        cfg = self._make_cfg({"ibm": {"api_key": "new-ibm-key"}})
        report = await seed_providers_from_yaml(
            db_fixture.dsn, db_fixture.fernet_key, cfg=cfg
        )
        assert report.updated == 1
        assert report.inserted == 0

        # Verify new value is stored encrypted (not plaintext)
        conn = await asyncpg.connect(db_fixture.dsn)
        try:
            row = await conn.fetchrow(
                "SELECT credential_value_encrypted FROM provider_credentials "
                "WHERE provider='ibm' AND name='default'"
            )
        finally:
            await conn.close()

        stored = row["credential_value_encrypted"]
        assert stored != "new-ibm-key", "Value must be stored encrypted"
        decrypted = fernet.decrypt(stored.encode()).decode()
        assert decrypted == "new-ibm-key"

    @pytest.mark.asyncio
    async def test_stored_values_are_not_plaintext(
        self, db_fixture, _clean_provider_credentials
    ):
        import asyncpg
        from cryptography.fernet import Fernet

        cfg = self._make_cfg(
            {"azure": {"client_secret": "super-secret-azure"}}
        )
        await seed_providers_from_yaml(db_fixture.dsn, db_fixture.fernet_key, cfg=cfg)

        conn = await asyncpg.connect(db_fixture.dsn)
        try:
            rows = await conn.fetch("SELECT credential_value_encrypted FROM provider_credentials")
        finally:
            await conn.close()

        fernet = Fernet(db_fixture.fernet_key.encode())
        for row in rows:
            val = row["credential_value_encrypted"]
            assert val != "super-secret-azure", "Plaintext stored — encryption failed"
            decrypted = fernet.decrypt(val.encode()).decode()
            assert decrypted == "super-secret-azure"

    @pytest.mark.asyncio
    async def test_idempotent_second_run_produces_no_changes(
        self, db_fixture, _clean_provider_credentials
    ):
        """Running the seeder twice with identical yaml must yield updated counts
        and zero inserts/deletes on the second pass."""
        cfg = self._make_cfg(
            {"ibm": {"api_key": "ibm-key"}}
        )
        r1 = await seed_providers_from_yaml(
            db_fixture.dsn, db_fixture.fernet_key, cfg=cfg
        )
        assert r1.inserted == 1
        assert r1.updated == 0

        r2 = await seed_providers_from_yaml(
            db_fixture.dsn, db_fixture.fernet_key, cfg=cfg
        )
        assert r2.inserted == 0
        assert r2.updated == 1
        assert r2.deleted == 0

    @pytest.mark.asyncio
    async def test_nosana_api_keys_list_synced(
        self, db_fixture, _clean_provider_credentials
    ):
        """Multiple nosana api_keys entries each become a separate DB row."""
        import asyncpg

        cfg = self._make_cfg(
            {
                "nosana": {
                    "api_keys": [
                        {"name": "prod", "key": "pk"},
                        {"name": "staging", "key": "sk"},
                    ]
                }
            }
        )
        report = await seed_providers_from_yaml(
            db_fixture.dsn, db_fixture.fernet_key, cfg=cfg
        )
        assert report.inserted == 2

        conn = await asyncpg.connect(db_fixture.dsn)
        try:
            rows = await conn.fetch(
                "SELECT name FROM provider_credentials WHERE provider='nosana'"
            )
        finally:
            await conn.close()

        names = {r["name"] for r in rows}
        assert names == {"prod", "staging"}

    @pytest.mark.asyncio
    async def test_azure_credentials_synced(
        self, db_fixture, _clean_provider_credentials
    ):
        """Azure credentials are all seeded correctly."""
        import asyncpg

        cfg = self._make_cfg(
            {
                "azure": {
                    "subscription_id": "sub-123",
                    "tenant_id": "tenant-xyz",
                    "client_id": "client-abc",
                    "client_secret": "secret-qwerty",
                }
            }
        )
        report = await seed_providers_from_yaml(
            db_fixture.dsn, db_fixture.fernet_key, cfg=cfg
        )
        assert report.inserted == 4

        conn = await asyncpg.connect(db_fixture.dsn)
        try:
            rows = await conn.fetch(
                "SELECT credential_type FROM provider_credentials WHERE provider='azure'"
            )
        finally:
            await conn.close()

        cred_types = {r["credential_type"] for r in rows}
        assert cred_types == {"subscription_id", "tenant_id", "client_id", "client_secret"}

    @pytest.mark.asyncio
    async def test_yaml_empty_providers_section_skips_delete(
        self, db_fixture, _clean_provider_credentials
    ):
        """If yaml.providers has no extractable rows but yaml exists, seeder
        still performs an upsert cycle (no deletes from nothing, no inserts)."""
        import asyncpg
        from cryptography.fernet import Fernet

        # Pre-seed a row manually
        fernet = Fernet(db_fixture.fernet_key.encode())
        conn = await asyncpg.connect(db_fixture.dsn)
        try:
            await conn.execute(
                """INSERT INTO provider_credentials
                   (provider, name, credential_type, credential_value_encrypted)
                   VALUES ('ibm', 'default', 'api_key', $1)""",
                fernet.encrypt(b"old-key").decode(),
            )
        finally:
            await conn.close()

        # Yaml with an empty providers tree
        cfg = self._make_cfg({})
        report = await seed_providers_from_yaml(
            db_fixture.dsn, db_fixture.fernet_key, cfg=cfg
        )
        # All existing rows should be deleted (yaml is authoritative, says nothing)
        assert report.deleted == 1
        assert report.inserted == 0

    @pytest.mark.asyncio
    async def test_is_active_false_preserved_in_db(
        self, db_fixture, _clean_provider_credentials
    ):
        """is_active=False from yaml must land in the DB as false."""
        import asyncpg

        cfg = self._make_cfg(
            {
                "nosana": {
                    "api_keys": [
                        {"name": "disabled", "key": "dk", "is_active": False}
                    ]
                }
            }
        )
        await seed_providers_from_yaml(db_fixture.dsn, db_fixture.fernet_key, cfg=cfg)

        conn = await asyncpg.connect(db_fixture.dsn)
        try:
            row = await conn.fetchrow(
                "SELECT is_active FROM provider_credentials "
                "WHERE provider='nosana' AND name='disabled'"
            )
        finally:
            await conn.close()

        assert row["is_active"] is False

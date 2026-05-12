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
                "cloud": {"aws": {"access_key_id": "", "secret_access_key": ""}},
                "vectordb": {"chroma": {"api_key": "", "tenant": "", "url": ""}},
                "guardrails": {"groq": {"api_key": ""}, "lakera": {"api_key": ""}},
                "depin": {
                    "nosana": {"wallet_private_key": "", "api_keys": []},
                    "akash": {"mnemonic": ""},
                },
            }
        )
        assert _extract_rows(p) == []

    def test_providers_all_none_values(self):
        p = _providers_from_dict(
            {
                "cloud": {"aws": {"access_key_id": None, "secret_access_key": None}},
                "guardrails": {"groq": {"api_key": None}},
                "depin": {"akash": {"mnemonic": None}},
            }
        )
        assert _extract_rows(p) == []

    def test_raw_dict_input(self):
        """_extract_rows also accepts a plain dict (no model_dump)."""
        result = _extract_rows({})
        assert result == []


# ---------------------------------------------------------------------------
# _extract_rows — cloud.aws
# ---------------------------------------------------------------------------

class TestExtractRowsAws:
    def test_access_key_id(self):
        p = _providers_from_dict({"cloud": {"aws": {"access_key_id": "AKIAIOSFODNN"}}})
        rows = _extract_rows(p)
        assert len(rows) == 1
        r = rows[0]
        assert r.provider == "aws"
        assert r.name == "default"
        assert r.credential_type == "access_key_id"
        assert r.value == "AKIAIOSFODNN"

    def test_secret_access_key(self):
        p = _providers_from_dict(
            {"cloud": {"aws": {"secret_access_key": "wJalrXUtnFEMI"}}}
        )
        rows = _extract_rows(p)
        assert len(rows) == 1
        assert rows[0].credential_type == "secret_access_key"

    def test_both_aws_fields(self):
        p = _providers_from_dict(
            {
                "cloud": {
                    "aws": {
                        "access_key_id": "AKIAIOSFODNN",
                        "secret_access_key": "secret",
                    }
                }
            }
        )
        rows = _extract_rows(p)
        assert len(rows) == 2
        types = {r.credential_type for r in rows}
        assert types == {"access_key_id", "secret_access_key"}


# ---------------------------------------------------------------------------
# _extract_rows — cloud.gcp
# ---------------------------------------------------------------------------

class TestExtractRowsGcp:
    def test_service_account_json(self):
        p = _providers_from_dict(
            {"cloud": {"gcp": {"service_account_json": '{"type":"service_account"}'}}}
        )
        rows = _extract_rows(p)
        assert len(rows) == 1
        assert rows[0].provider == "gcp"
        assert rows[0].credential_type == "service_account_json"


# ---------------------------------------------------------------------------
# _extract_rows — vectordb.chroma
# ---------------------------------------------------------------------------

class TestExtractRowsChroma:
    def test_all_chroma_fields(self):
        p = _providers_from_dict(
            {
                "vectordb": {
                    "chroma": {
                        "api_key": "ck-key",
                        "tenant": "my-tenant",
                        "url": "https://chroma.example.com",
                        "database": "mydb",
                    }
                }
            }
        )
        rows = _extract_rows(p)
        assert len(rows) == 4
        cred_types = {r.credential_type for r in rows}
        assert cred_types == {"api_key", "tenant", "url", "database"}

    def test_partial_chroma(self):
        p = _providers_from_dict(
            {"vectordb": {"chroma": {"api_key": "ck-key", "tenant": ""}}}
        )
        rows = _extract_rows(p)
        assert len(rows) == 1
        assert rows[0].credential_type == "api_key"


# ---------------------------------------------------------------------------
# _extract_rows — guardrails
# ---------------------------------------------------------------------------

class TestExtractRowsGuardrails:
    def test_groq_api_key(self):
        p = _providers_from_dict({"guardrails": {"groq": {"api_key": "groq-secret"}}})
        rows = _extract_rows(p)
        assert len(rows) == 1
        assert rows[0].provider == "groq"
        assert rows[0].credential_type == "api_key"

    def test_lakera_api_key(self):
        p = _providers_from_dict(
            {"guardrails": {"lakera": {"api_key": "lakera-key"}}}
        )
        rows = _extract_rows(p)
        assert len(rows) == 1
        assert rows[0].provider == "lakera"

    def test_both_guardrail_providers(self):
        p = _providers_from_dict(
            {
                "guardrails": {
                    "groq": {"api_key": "g"},
                    "lakera": {"api_key": "l"},
                }
            }
        )
        rows = _extract_rows(p)
        assert len(rows) == 2
        providers = {r.provider for r in rows}
        assert providers == {"groq", "lakera"}


# ---------------------------------------------------------------------------
# _extract_rows — depin.nosana (wallet + api_keys list)
# ---------------------------------------------------------------------------

class TestExtractRowsNosana:
    def test_wallet_private_key(self):
        p = _providers_from_dict(
            {"depin": {"nosana": {"wallet_private_key": "abc123"}}}
        )
        rows = _extract_rows(p)
        assert len(rows) == 1
        assert rows[0].provider == "nosana"
        assert rows[0].name == "wallet"
        assert rows[0].credential_type == "wallet_private_key"

    def test_single_api_key(self):
        p = _providers_from_dict(
            {
                "depin": {
                    "nosana": {
                        "api_keys": [{"name": "prod", "key": "prod-key"}]
                    }
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
                "depin": {
                    "nosana": {
                        "api_keys": [
                            {"name": "prod", "key": "prod-key"},
                            {"name": "staging", "key": "staging-key"},
                            {"name": "dev", "key": "dev-key"},
                        ]
                    }
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
                "depin": {
                    "nosana": {
                        "api_keys": [
                            {"name": "disabled", "key": "some-key", "is_active": False}
                        ]
                    }
                }
            }
        )
        rows = _extract_rows(p)
        assert len(rows) == 1
        assert rows[0].is_active is False

    def test_api_key_is_active_defaults_true(self):
        p = _providers_from_dict(
            {
                "depin": {
                    "nosana": {
                        "api_keys": [{"name": "nostate", "key": "k"}]
                    }
                }
            }
        )
        rows = _extract_rows(p)
        assert rows[0].is_active is True

    def test_api_keys_entry_missing_name_skipped(self):
        p = _providers_from_dict(
            {
                "depin": {
                    "nosana": {
                        "api_keys": [
                            {"key": "orphan-key"},  # no name
                            {"name": "ok", "key": "good-key"},
                        ]
                    }
                }
            }
        )
        rows = _extract_rows(p)
        assert len(rows) == 1
        assert rows[0].name == "ok"

    def test_api_keys_entry_missing_key_skipped(self):
        p = _providers_from_dict(
            {
                "depin": {
                    "nosana": {
                        "api_keys": [
                            {"name": "novalue"},  # no key
                        ]
                    }
                }
            }
        )
        rows = _extract_rows(p)
        assert rows == []

    def test_api_keys_entry_empty_key_skipped(self):
        p = _providers_from_dict(
            {
                "depin": {
                    "nosana": {
                        "api_keys": [{"name": "x", "key": ""}]
                    }
                }
            }
        )
        rows = _extract_rows(p)
        assert rows == []

    def test_api_keys_non_dict_entry_skipped(self):
        p = _providers_from_dict(
            {
                "depin": {
                    "nosana": {
                        "api_keys": ["not-a-dict", {"name": "good", "key": "v"}]
                    }
                }
            }
        )
        rows = _extract_rows(p)
        assert len(rows) == 1

    def test_wallet_and_api_keys_combined(self):
        p = _providers_from_dict(
            {
                "depin": {
                    "nosana": {
                        "wallet_private_key": "wallet-secret",
                        "api_keys": [
                            {"name": "prod", "key": "prod-key"},
                        ],
                    }
                }
            }
        )
        rows = _extract_rows(p)
        assert len(rows) == 2
        cred_types = {r.credential_type for r in rows}
        assert cred_types == {"wallet_private_key", "api_key"}


# ---------------------------------------------------------------------------
# _extract_rows — depin.akash
# ---------------------------------------------------------------------------

class TestExtractRowsAkash:
    def test_mnemonic(self):
        p = _providers_from_dict(
            {"depin": {"akash": {"mnemonic": "word1 word2 word3"}}}
        )
        rows = _extract_rows(p)
        assert len(rows) == 1
        assert rows[0].provider == "akash"
        assert rows[0].name == "default"
        assert rows[0].credential_type == "mnemonic"

    def test_whitespace_mnemonic_skipped(self):
        p = _providers_from_dict({"depin": {"akash": {"mnemonic": "   "}}})
        assert _extract_rows(p) == []


# ---------------------------------------------------------------------------
# _extract_rows — full tree
# ---------------------------------------------------------------------------

class TestExtractRowsFullTree:
    def test_all_branches(self):
        p = _providers_from_dict(
            {
                "cloud": {
                    "aws": {
                        "access_key_id": "AKIA",
                        "secret_access_key": "secret",
                    },
                    "gcp": {"service_account_json": "{}"},
                },
                "vectordb": {
                    "chroma": {
                        "api_key": "ck",
                        "tenant": "t",
                        "url": "http://localhost",
                        "database": "db",
                    }
                },
                "guardrails": {
                    "groq": {"api_key": "groq"},
                    "lakera": {"api_key": "lakera"},
                },
                "depin": {
                    "nosana": {
                        "wallet_private_key": "wallet",
                        "api_keys": [
                            {"name": "prod", "key": "pk"},
                            {"name": "staging", "key": "sk"},
                        ],
                    },
                    "akash": {"mnemonic": "w1 w2"},
                },
            }
        )
        rows = _extract_rows(p)
        # 2 (aws) + 1 (gcp) + 4 (chroma) + 1 (groq) + 1 (lakera) +
        # 1 (wallet) + 2 (api_keys) + 1 (akash) = 13
        assert len(rows) == 13


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
                "cloud": {"aws": {"access_key_id": "AKIA", "secret_access_key": "S"}},
                "guardrails": {"groq": {"api_key": "groq-key"}},
            }
        )
        report = await seed_providers_from_yaml(
            db_fixture.dsn, db_fixture.fernet_key, cfg=cfg
        )
        assert report.skipped is False
        assert report.inserted == 3  # access_key_id + secret_access_key + groq
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

        # Yaml only has groq; old-provider should be deleted
        cfg = self._make_cfg({"guardrails": {"groq": {"api_key": "g"}}})
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
                   VALUES ('groq', 'default', 'api_key', $1)""",
                fernet.encrypt(b"old-key").decode(),
            )
        finally:
            await conn.close()

        cfg = self._make_cfg({"guardrails": {"groq": {"api_key": "new-groq-key"}}})
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
                "WHERE provider='groq' AND name='default'"
            )
        finally:
            await conn.close()

        stored = row["credential_value_encrypted"]
        assert stored != "new-groq-key", "Value must be stored encrypted"
        decrypted = fernet.decrypt(stored.encode()).decode()
        assert decrypted == "new-groq-key"

    @pytest.mark.asyncio
    async def test_stored_values_are_not_plaintext(
        self, db_fixture, _clean_provider_credentials
    ):
        import asyncpg
        from cryptography.fernet import Fernet

        cfg = self._make_cfg(
            {"guardrails": {"groq": {"api_key": "super-secret-groq"}}}
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
            assert val != "super-secret-groq", "Plaintext stored — encryption failed"
            decrypted = fernet.decrypt(val.encode()).decode()
            assert decrypted == "super-secret-groq"

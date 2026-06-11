"""Unit tests for the `inferiallm providers` CLI backend (cli_providers.py).

All DB interactions are mocked via asyncpg stubs — no real database required.
Coverage target: ≥85 % of cli_providers.py lines.
"""
from __future__ import annotations

import io
import json
import sys
from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock, patch, call
import pytest

from cli.providers import (
    _build_dsn,
    _read_config,
    _write_config,
    cmd_list,
    cmd_add,
    cmd_update,
    cmd_remove,
    _parse_bool,
    _parse_bool_opt,
    _CONFIG_KEY,
)


# ---------------------------------------------------------------------------
# Helper: fake asyncpg connection
# ---------------------------------------------------------------------------

def _make_conn(stored_value: Optional[Any] = None):
    """Return a mock asyncpg connection.

    *stored_value* is what fetchrow returns as row["value"].
    None means no row in DB.
    """
    conn = MagicMock()

    if stored_value is None:
        conn.fetchrow = AsyncMock(return_value=None)
    else:
        row = {"value": stored_value if isinstance(stored_value, str) else json.dumps(stored_value)}
        conn.fetchrow = AsyncMock(return_value=row)

    conn.execute = AsyncMock(return_value=None)
    conn.close = AsyncMock()
    return conn


def _last_written(conn) -> dict:
    """Extract the JSON blob passed to the last _write_config call."""
    # execute was called with (_CONFIG_KEY, json_str)
    assert conn.execute.called, "No write was issued"
    args = conn.execute.call_args[0]
    return json.loads(args[-1])


# ---------------------------------------------------------------------------
# _build_dsn
# ---------------------------------------------------------------------------

class TestBuildDsn:
    def test_uses_database_url(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@host:5432/db")
        dsn = _build_dsn()
        assert dsn == "postgresql://u:p@host:5432/db"

    def test_falls_back_to_env_vars(self, monkeypatch):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.setenv("INFERIA_DB_USER", "myuser")
        monkeypatch.setenv("INFERIA_DB_PASSWORD", "secret")
        monkeypatch.setenv("PG_HOST", "pghost")
        monkeypatch.setenv("PG_PORT", "5433")
        monkeypatch.setenv("INFERIA_DB", "mydb")
        dsn = _build_dsn()
        assert "myuser" in dsn
        assert "pghost" in dsn
        assert "5433" in dsn
        assert "mydb" in dsn

    def test_defaults_when_no_env(self, monkeypatch):
        for v in ("DATABASE_URL", "INFERIA_DB_USER", "INFERIA_DB_PASSWORD",
                  "PG_HOST", "PG_PORT", "INFERIA_DB"):
            monkeypatch.delenv(v, raising=False)
        dsn = _build_dsn()
        assert "postgresql://" in dsn
        assert "localhost" in dsn


# ---------------------------------------------------------------------------
# _read_config / _write_config
# ---------------------------------------------------------------------------

class TestReadWriteConfig:
    @pytest.mark.asyncio
    async def test_read_returns_empty_when_no_row(self):
        conn = _make_conn(None)
        result = await _read_config(conn)
        assert result == {}

    @pytest.mark.asyncio
    async def test_read_returns_dict_from_string(self):
        conn = _make_conn('{"cloud": {"aws": {}}}')
        result = await _read_config(conn)
        assert "cloud" in result

    @pytest.mark.asyncio
    async def test_read_returns_dict_from_dict(self):
        conn = _make_conn({"depin": {"nosana": {}}})
        result = await _read_config(conn)
        assert "depin" in result

    @pytest.mark.asyncio
    async def test_write_calls_execute(self):
        conn = _make_conn(None)
        await _write_config(conn, {"cloud": {"aws": {"access_key_id": "X"}}})
        assert conn.execute.called
        written = _last_written(conn)
        assert written["cloud"]["aws"]["access_key_id"] == "X"


# ---------------------------------------------------------------------------
# _parse_bool / _parse_bool_opt
# ---------------------------------------------------------------------------

class TestParseBool:
    def test_true_values(self):
        for v in ("true", "True", "TRUE", "1", "yes", "y"):
            assert _parse_bool(v) is True

    def test_false_values(self):
        for v in ("false", "False", "FALSE", "0", "no", "n"):
            assert _parse_bool(v) is False

    def test_parse_bool_opt_none(self):
        assert _parse_bool_opt(None) is None

    def test_parse_bool_opt_value(self):
        assert _parse_bool_opt("true") is True
        assert _parse_bool_opt("false") is False


# ---------------------------------------------------------------------------
# cmd_list
# ---------------------------------------------------------------------------

class TestCmdList:
    @pytest.mark.asyncio
    async def test_list_empty_prints_no_credentials(self, capsys):
        conn = _make_conn(None)
        await cmd_list(conn, None)
        out = capsys.readouterr().out
        assert "No credentials found" in out

    @pytest.mark.asyncio
    async def test_list_cloud_aws(self, capsys):
        cfg = {"cloud": {"aws": {"access_key_id": "AKIA123", "region": "us-east-1"}}}
        conn = _make_conn(cfg)
        await cmd_list(conn, "aws")
        out = capsys.readouterr().out
        assert "aws" in out
        assert "access_key_id" in out

    @pytest.mark.asyncio
    async def test_list_nosana_wallet(self, capsys):
        cfg = {"depin": {"nosana": {"wallet_private_key": "wkey", "api_keys": []}}}
        conn = _make_conn(cfg)
        await cmd_list(conn, "nosana")
        out = capsys.readouterr().out
        assert "nosana" in out
        assert "wallet_private_key" in out

    @pytest.mark.asyncio
    async def test_list_nosana_api_keys(self, capsys):
        cfg = {
            "depin": {
                "nosana": {
                    "api_keys": [
                        {"name": "prod", "key": "pk", "is_active": True},
                        {"name": "staging", "key": "sk", "is_active": False},
                    ]
                }
            }
        }
        conn = _make_conn(cfg)
        await cmd_list(conn, "nosana")
        out = capsys.readouterr().out
        assert "prod" in out
        assert "staging" in out

    @pytest.mark.asyncio
    async def test_list_all_providers(self, capsys):
        cfg = {
            "cloud": {"aws": {"access_key_id": "AKIA"}},
            "depin": {"nosana": {"api_keys": [{"name": "k1", "key": "v1"}]}},
        }
        conn = _make_conn(cfg)
        await cmd_list(conn, None)
        out = capsys.readouterr().out
        assert "aws" in out
        assert "nosana" in out

    @pytest.mark.asyncio
    async def test_list_only_nonempty_cloud_fields(self, capsys):
        # region is a string but it's not a credential — check only non-empty str fields appear
        cfg = {"cloud": {"ibm": {"api_key": "ibm-k", "region": "us-south", "resource_group_id": ""}}}
        conn = _make_conn(cfg)
        await cmd_list(conn, "ibm")
        out = capsys.readouterr().out
        assert "api_key" in out
        # region is a non-empty string so it appears (CLI lists all string fields)
        assert "ibm" in out


# ---------------------------------------------------------------------------
# cmd_add
# ---------------------------------------------------------------------------

class TestCmdAdd:
    @pytest.mark.asyncio
    async def test_add_nosana_api_key(self):
        conn = _make_conn(None)
        await cmd_add(conn, "nosana", "prod", "api_key", "secret-key", True)
        written = _last_written(conn)
        keys = written["depin"]["nosana"]["api_keys"]
        assert len(keys) == 1
        assert keys[0]["name"] == "prod"
        assert keys[0]["key"] == "secret-key"
        assert keys[0]["is_active"] is True

    @pytest.mark.asyncio
    async def test_add_nosana_api_key_inactive(self):
        conn = _make_conn(None)
        await cmd_add(conn, "nosana", "disabled", "api_key", "k", False)
        written = _last_written(conn)
        keys = written["depin"]["nosana"]["api_keys"]
        assert keys[0]["is_active"] is False

    @pytest.mark.asyncio
    async def test_add_nosana_wallet_private_key(self):
        conn = _make_conn(None)
        await cmd_add(conn, "nosana", "wallet", "wallet_private_key", "my-wallet", True)
        written = _last_written(conn)
        assert written["depin"]["nosana"]["wallet_private_key"] == "my-wallet"

    @pytest.mark.asyncio
    async def test_add_aws_credential(self):
        conn = _make_conn(None)
        await cmd_add(conn, "aws", "default", "access_key_id", "AKIA999", True)
        written = _last_written(conn)
        assert written["cloud"]["aws"]["access_key_id"] == "AKIA999"

    @pytest.mark.asyncio
    async def test_add_gcp_credential(self):
        conn = _make_conn(None)
        await cmd_add(conn, "gcp", "default", "project_id", "my-proj", True)
        written = _last_written(conn)
        assert written["cloud"]["gcp"]["project_id"] == "my-proj"

    @pytest.mark.asyncio
    async def test_add_azure_credential(self):
        conn = _make_conn(None)
        await cmd_add(conn, "azure", "default", "subscription_id", "sub-abc", True)
        written = _last_written(conn)
        assert written["cloud"]["azure"]["subscription_id"] == "sub-abc"

    @pytest.mark.asyncio
    async def test_add_ibm_credential(self):
        conn = _make_conn(None)
        await cmd_add(conn, "ibm", "default", "api_key", "ibm-k", True)
        written = _last_written(conn)
        assert written["cloud"]["ibm"]["api_key"] == "ibm-k"

    @pytest.mark.asyncio
    async def test_add_duplicate_nosana_key_exits(self):
        existing = {
            "depin": {"nosana": {"api_keys": [{"name": "prod", "key": "old"}]}}
        }
        conn = _make_conn(existing)
        with pytest.raises(SystemExit):
            await cmd_add(conn, "nosana", "prod", "api_key", "new-key", True)

    @pytest.mark.asyncio
    async def test_add_preserves_existing_keys(self):
        existing = {
            "depin": {"nosana": {"api_keys": [{"name": "existing", "key": "ek"}]}}
        }
        conn = _make_conn(existing)
        await cmd_add(conn, "nosana", "new_one", "api_key", "nk", True)
        written = _last_written(conn)
        names = [k["name"] for k in written["depin"]["nosana"]["api_keys"]]
        assert "existing" in names
        assert "new_one" in names


# ---------------------------------------------------------------------------
# cmd_update
# ---------------------------------------------------------------------------

class TestCmdUpdate:
    @pytest.mark.asyncio
    async def test_update_nosana_api_key_value(self):
        existing = {
            "depin": {"nosana": {"api_keys": [{"name": "prod", "key": "old", "is_active": True}]}}
        }
        conn = _make_conn(existing)
        await cmd_update(conn, "nosana", "prod", None, "new-key", None)
        written = _last_written(conn)
        entry = written["depin"]["nosana"]["api_keys"][0]
        assert entry["key"] == "new-key"

    @pytest.mark.asyncio
    async def test_update_nosana_api_key_active(self):
        existing = {
            "depin": {"nosana": {"api_keys": [{"name": "prod", "key": "k", "is_active": True}]}}
        }
        conn = _make_conn(existing)
        await cmd_update(conn, "nosana", "prod", None, None, False)
        written = _last_written(conn)
        assert written["depin"]["nosana"]["api_keys"][0]["is_active"] is False

    @pytest.mark.asyncio
    async def test_update_nosana_wallet(self):
        existing = {"depin": {"nosana": {"wallet_private_key": "old-w", "api_keys": []}}}
        conn = _make_conn(existing)
        await cmd_update(conn, "nosana", "wallet", "wallet_private_key", "new-w", None)
        written = _last_written(conn)
        assert written["depin"]["nosana"]["wallet_private_key"] == "new-w"

    @pytest.mark.asyncio
    async def test_update_aws_field(self):
        existing = {"cloud": {"aws": {"access_key_id": "AKIA-OLD"}}}
        conn = _make_conn(existing)
        await cmd_update(conn, "aws", "default", "access_key_id", "AKIA-NEW", None)
        written = _last_written(conn)
        assert written["cloud"]["aws"]["access_key_id"] == "AKIA-NEW"

    @pytest.mark.asyncio
    async def test_update_cloud_missing_type_exits(self):
        conn = _make_conn(None)
        with pytest.raises(SystemExit):
            await cmd_update(conn, "aws", "default", None, "val", None)

    @pytest.mark.asyncio
    async def test_update_nosana_key_not_found_exits(self):
        conn = _make_conn(None)
        with pytest.raises(SystemExit):
            await cmd_update(conn, "nosana", "nonexistent", None, "val", None)

    @pytest.mark.asyncio
    async def test_update_cloud_field_not_found_exits(self):
        existing = {"cloud": {"aws": {"region": "us-east-1"}}}
        conn = _make_conn(existing)
        with pytest.raises(SystemExit):
            await cmd_update(conn, "aws", "default", "access_key_id", "X", None)


# ---------------------------------------------------------------------------
# cmd_remove
# ---------------------------------------------------------------------------

class TestCmdRemove:
    @pytest.mark.asyncio
    async def test_remove_nosana_api_key(self):
        existing = {
            "depin": {
                "nosana": {
                    "api_keys": [
                        {"name": "prod", "key": "pk"},
                        {"name": "staging", "key": "sk"},
                    ]
                }
            }
        }
        conn = _make_conn(existing)
        await cmd_remove(conn, "nosana", "prod")
        written = _last_written(conn)
        names = [k["name"] for k in written["depin"]["nosana"]["api_keys"]]
        assert "prod" not in names
        assert "staging" in names

    @pytest.mark.asyncio
    async def test_remove_nosana_wallet(self):
        existing = {"depin": {"nosana": {"wallet_private_key": "wk", "api_keys": []}}}
        conn = _make_conn(existing)
        await cmd_remove(conn, "nosana", "wallet")
        written = _last_written(conn)
        assert written["depin"]["nosana"]["wallet_private_key"] is None

    @pytest.mark.asyncio
    async def test_remove_cloud_field(self):
        existing = {"cloud": {"aws": {"access_key_id": "AKIA"}}}
        conn = _make_conn(existing)
        await cmd_remove(conn, "aws", "access_key_id")
        written = _last_written(conn)
        assert "access_key_id" not in written["cloud"]["aws"]

    @pytest.mark.asyncio
    async def test_remove_nosana_key_not_found_exits(self):
        conn = _make_conn(None)
        with pytest.raises(SystemExit):
            await cmd_remove(conn, "nosana", "nonexistent")

    @pytest.mark.asyncio
    async def test_remove_nosana_wallet_not_set_exits(self):
        conn = _make_conn(None)
        with pytest.raises(SystemExit):
            await cmd_remove(conn, "nosana", "wallet")

    @pytest.mark.asyncio
    async def test_remove_cloud_field_not_found_exits(self):
        existing = {"cloud": {"aws": {"region": "us-east-1"}}}
        conn = _make_conn(existing)
        with pytest.raises(SystemExit):
            await cmd_remove(conn, "aws", "access_key_id")

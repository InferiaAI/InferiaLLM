"""
Tests for SQL injection prevention in cli_init.py.

Verifies that the database password is properly escaped via
PostgreSQL's quote_literal() when creating the inferia role,
preventing SQL injection via crafted passwords.
"""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
import asyncio

from inferia.cli_init import _safe_ident


class TestSafeIdent:
    """Verify _safe_ident rejects dangerous SQL identifiers."""

    def test_valid_identifier(self):
        assert _safe_ident("inferia") == "inferia"
        assert _safe_ident("my_db") == "my_db"
        assert _safe_ident("_private") == "_private"
        assert _safe_ident("User123") == "User123"

    def test_rejects_sql_injection_in_identifier(self):
        with pytest.raises(ValueError, match="Unsafe SQL identifier"):
            _safe_ident("inferia; DROP TABLE users;--")

    def test_rejects_quotes_in_identifier(self):
        with pytest.raises(ValueError, match="Unsafe SQL identifier"):
            _safe_ident("admin'--")

    def test_rejects_spaces_in_identifier(self):
        with pytest.raises(ValueError, match="Unsafe SQL identifier"):
            _safe_ident("my database")

    def test_rejects_leading_digit(self):
        with pytest.raises(ValueError, match="Unsafe SQL identifier"):
            _safe_ident("1admin")

    def test_rejects_empty_string(self):
        assert _safe_ident("") == ""


class TestPasswordEscaping:
    """Verify that role creation uses quote_literal for the password."""

    def test_create_role_calls_quote_literal(self):
        """_init must call quote_literal($1) with the password before CREATE ROLE."""
        from inferia.cli_init import _init

        mock_conn = AsyncMock()
        mock_conn.fetchval = AsyncMock(side_effect=lambda q, *args: (
            # Simulate quote_literal() — returns the escaped literal
            f"'{args[0]}'" if "quote_literal" in q else None
        ))
        mock_conn.execute = AsyncMock()

        # Simulate: role does not exist, db does not exist
        call_count = {"fetchval": 0}
        original_fetchval = mock_conn.fetchval

        async def fetchval_router(query, *args):
            call_count["fetchval"] += 1
            if "pg_roles" in query:
                return None  # role does not exist
            if "pg_database" in query:
                return 1  # db exists (skip CREATE DATABASE)
            if "quote_literal" in query:
                # Simulate PostgreSQL quote_literal: escape single quotes
                val = args[0].replace("'", "''")
                return f"'{val}'"
            return None

        mock_conn.fetchval = AsyncMock(side_effect=fetchval_router)

        env = {
            "PG_ADMIN_USER": "admin",
            "PG_ADMIN_PASSWORD": "adminpw",
            "INFERIA_DB_USER": "inferia",
            "INFERIA_DB_PASSWORD": "pass'word;DROP TABLE users;--",
            "PG_HOST": "localhost",
            "PG_PORT": "5432",
            "INFERIA_DB": "inferia",
        }

        with (
            patch("inferia.cli_init.asyncpg") as mock_asyncpg,
            patch.dict("os.environ", env, clear=True),
            patch("inferia.cli_init._bootstrap_api_gateway"),
            patch("inferia.cli_init._execute_schema", new_callable=AsyncMock),
        ):
            mock_asyncpg.connect = AsyncMock(return_value=mock_conn)

            asyncio.get_event_loop().run_until_complete(_init())

            # Verify quote_literal was called with the raw password
            quote_literal_calls = [
                call for call in mock_conn.fetchval.call_args_list
                if "quote_literal" in str(call)
            ]
            assert len(quote_literal_calls) == 1, (
                "quote_literal must be called exactly once for the password"
            )
            assert quote_literal_calls[0].args[1] == "pass'word;DROP TABLE users;--"

    def test_create_role_uses_escaped_password(self):
        """The CREATE ROLE statement must use the escaped password, not the raw one."""
        from inferia.cli_init import _init

        mock_conn = AsyncMock()
        executed_statements = []

        async def fetchval_router(query, *args):
            if "pg_roles" in query:
                return None  # role does not exist
            if "pg_database" in query:
                return 1  # db exists
            if "quote_literal" in query:
                val = args[0].replace("'", "''")
                return f"'{val}'"
            return None

        async def capture_execute(query, *args):
            executed_statements.append(query)

        mock_conn.fetchval = AsyncMock(side_effect=fetchval_router)
        mock_conn.execute = AsyncMock(side_effect=capture_execute)

        env = {
            "PG_ADMIN_USER": "admin",
            "PG_ADMIN_PASSWORD": "adminpw",
            "INFERIA_DB_USER": "inferia",
            "INFERIA_DB_PASSWORD": "pass'word",
            "PG_HOST": "localhost",
            "PG_PORT": "5432",
            "INFERIA_DB": "inferia",
        }

        with (
            patch("inferia.cli_init.asyncpg") as mock_asyncpg,
            patch.dict("os.environ", env, clear=True),
            patch("inferia.cli_init._bootstrap_api_gateway"),
            patch("inferia.cli_init._execute_schema", new_callable=AsyncMock),
        ):
            mock_asyncpg.connect = AsyncMock(return_value=mock_conn)

            asyncio.get_event_loop().run_until_complete(_init())

            # Find the CREATE ROLE statement
            create_role_stmts = [
                s for s in executed_statements if "CREATE ROLE" in s
            ]
            assert len(create_role_stmts) == 1

            stmt = create_role_stmts[0]
            # The password must be escaped (single quote doubled)
            assert "pass''word" in stmt, (
                f"Password was not escaped in CREATE ROLE. Statement: {stmt}"
            )
            # The raw unescaped password must NOT appear
            assert "pass'word'" not in stmt or "pass''word" in stmt

    def test_normal_password_works(self):
        """A normal password without special chars should work fine."""
        from inferia.cli_init import _init

        mock_conn = AsyncMock()
        executed_statements = []

        async def fetchval_router(query, *args):
            if "pg_roles" in query:
                return None
            if "pg_database" in query:
                return 1
            if "quote_literal" in query:
                return f"'{args[0]}'"
            return None

        async def capture_execute(query, *args):
            executed_statements.append(query)

        mock_conn.fetchval = AsyncMock(side_effect=fetchval_router)
        mock_conn.execute = AsyncMock(side_effect=capture_execute)

        env = {
            "PG_ADMIN_USER": "admin",
            "PG_ADMIN_PASSWORD": "adminpw",
            "INFERIA_DB_USER": "inferia",
            "INFERIA_DB_PASSWORD": "securepassword123",
            "PG_HOST": "localhost",
            "PG_PORT": "5432",
            "INFERIA_DB": "inferia",
        }

        with (
            patch("inferia.cli_init.asyncpg") as mock_asyncpg,
            patch.dict("os.environ", env, clear=True),
            patch("inferia.cli_init._bootstrap_api_gateway"),
            patch("inferia.cli_init._execute_schema", new_callable=AsyncMock),
        ):
            mock_asyncpg.connect = AsyncMock(return_value=mock_conn)

            asyncio.get_event_loop().run_until_complete(_init())

            create_role_stmts = [
                s for s in executed_statements if "CREATE ROLE" in s
            ]
            assert len(create_role_stmts) == 1
            assert "securepassword123" in create_role_stmts[0]

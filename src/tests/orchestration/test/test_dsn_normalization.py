"""Orchestration must hand asyncpg a bare postgresql:// DSN.

`asyncpg.create_pool` rejects SQLAlchemy-style schemes (`postgresql+asyncpg://`)
with "invalid DSN: scheme is expected to be either postgresql or postgres". The
orchestration Settings strips any `+driver` from DATABASE_URL so a single shared
DATABASE_URL (which the api_gateway uses by adding +asyncpg itself) works here.
"""

from orchestration.config import Settings


def _dsn(value: str) -> str:
    # init kwargs are highest precedence and run through the field validator.
    return Settings(DATABASE_URL=value).postgres_dsn


def test_strips_asyncpg_driver():
    assert _dsn("postgresql+asyncpg://u:p@h:5432/db") == "postgresql://u:p@h:5432/db"


def test_strips_other_drivers():
    assert _dsn("postgresql+psycopg2://u:p@h/db") == "postgresql://u:p@h/db"


def test_bare_postgresql_unchanged():
    assert _dsn("postgresql://u:p@h:5432/db") == "postgresql://u:p@h:5432/db"


def test_short_postgres_scheme_unchanged():
    assert _dsn("postgres://u:p@h/db") == "postgres://u:p@h/db"


def test_password_with_special_chars_preserved():
    # the split is on the first "://" only, so credentials/paths are untouched.
    assert _dsn("postgresql+asyncpg://u:p@ss@h/db") == "postgresql://u:p@ss@h/db"

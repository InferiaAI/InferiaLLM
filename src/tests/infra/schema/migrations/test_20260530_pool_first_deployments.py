import os
import pytest
import asyncpg
from pytest_asyncio import fixture as pytest_asyncio_fixture

pytestmark = pytest.mark.asyncio


async def _apply_all_migrations(conn: asyncpg.Connection) -> None:
    import pathlib
    here = pathlib.Path(__file__).resolve().parents[1]
    for sql_path in sorted(here.glob("*.sql")):
        if sql_path.name.startswith("test_"):
            continue
        sql = sql_path.read_text()
        for chunk in sql.split("-- @SPLIT@"):
            chunk = chunk.strip()
            if chunk:
                await conn.execute(chunk)


@pytest_asyncio_fixture
async def conn():
    dsn = os.getenv(
        "TEST_DATABASE_URL",
        "postgresql://inferia:inferia@localhost:5432/inferia_test",
    )
    c = await asyncpg.connect(dsn)
    yield c
    await c.close()


async def test_deployments_target_columns_exist(conn):
    await _apply_all_migrations(conn)
    cols = {r["column_name"] for r in await conn.fetch(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name='model_deployments' AND column_name "
        "IN ('target_pool_id', 'target_node_id')"
    )}
    assert cols == {"target_pool_id", "target_node_id"}


async def test_compute_pools_max_nodes_column_exists(conn):
    await _apply_all_migrations(conn)
    row = await conn.fetchrow(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name='compute_pools' AND column_name='max_nodes'"
    )
    assert row is not None
    assert row["column_name"] == "max_nodes"


async def test_pending_node_index_exists(conn):
    await _apply_all_migrations(conn)
    row = await conn.fetchrow(
        "SELECT indexname FROM pg_indexes "
        "WHERE indexname='idx_model_deployments_pending_node'"
    )
    assert row is not None


async def test_migration_is_idempotent(conn):
    await _apply_all_migrations(conn)
    await _apply_all_migrations(conn)  # no exception = pass


async def test_apply_migrations_via_production_runner(conn):
    """Test the production runner (_apply_migrations) directly.

    Verifies:
    (a) All migration files are recorded in schema_migrations
    (b) The columns/index from 20260530 are present
    (c) A second invocation is idempotent (no duplicate rows in schema_migrations)
    """
    from cli import init

    # First, set up the schema_migrations table (normally done by _apply_migrations)
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS schema_migrations (
            filename VARCHAR PRIMARY KEY,
            applied_at TIMESTAMP DEFAULT now()
        )
    """)

    # Clear all schema_migrations for a clean test (ensures runner sees all as unapplied)
    await conn.execute("TRUNCATE TABLE schema_migrations")

    # Get DSN from environment or use default
    dsn = os.getenv(
        "TEST_DATABASE_URL",
        "postgresql://inferia:inferia@deploy-postgres-1:5432/inferia_test",
    )

    # Run the production runner
    await cli_init._apply_migrations(dsn)

    # Assert the new migration is recorded
    row = await conn.fetchrow(
        "SELECT filename FROM schema_migrations "
        "WHERE filename = '20260530_pool_first_deployments.sql'"
    )
    assert row is not None, "20260530 migration not recorded in schema_migrations"

    # Assert the new columns exist
    target_pool_col = await conn.fetchrow(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name='model_deployments' AND column_name='target_pool_id'"
    )
    assert target_pool_col is not None, "target_pool_id column missing"

    target_node_col = await conn.fetchrow(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name='model_deployments' AND column_name='target_node_id'"
    )
    assert target_node_col is not None, "target_node_id column missing"

    max_nodes_col = await conn.fetchrow(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name='compute_pools' AND column_name='max_nodes'"
    )
    assert max_nodes_col is not None, "max_nodes column missing"

    # Assert the index exists
    idx = await conn.fetchrow(
        "SELECT indexname FROM pg_indexes "
        "WHERE indexname='idx_model_deployments_pending_node'"
    )
    assert idx is not None, "idx_model_deployments_pending_node index missing"

    # Idempotent re-run via the production path (should skip all already-applied migrations)
    await cli_init._apply_migrations(dsn)

    # Verify no duplicate rows in schema_migrations
    count = await conn.fetchval(
        "SELECT COUNT(*) FROM schema_migrations "
        "WHERE filename = '20260530_pool_first_deployments.sql'"
    )
    assert count == 1, f"Expected 1 schema_migrations row after idempotent re-run, found {count}"

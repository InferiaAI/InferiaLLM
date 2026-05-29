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

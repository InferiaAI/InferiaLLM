import asyncpg
import os


async def create_test_db_pool():
    """
    Creates a PostgreSQL connection pool for tests.
    Uses the same env vars as main.py.
    """

    return await asyncpg.create_pool(
        host=os.getenv("POSTGRES_HOST", "localhost"),
        port=int(os.getenv("POSTGRES_PORT") or 5432),
        user=os.getenv("POSTGRES_USER", "inferia"),
        password=os.getenv("POSTGRES_PASSWORD", "inferia"),
        database=os.getenv("POSTGRES_DB", "inferia"),
        min_size=1,
        max_size=5,
    )

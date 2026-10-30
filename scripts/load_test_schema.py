"""Build the test database the way `inferiallm init` does: global schema, then migrations.

Usage: TEST_DATABASE_URL=postgresql://... python scripts/load_test_schema.py
"""
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cli.init import SCHEMA_DIR, _apply_migrations, _execute_schema  # noqa: E402


async def main() -> None:
    dsn = os.environ["TEST_DATABASE_URL"]
    await _execute_schema(dsn, SCHEMA_DIR / "global_schema.sql", "global_schema")
    await _apply_migrations(dsn)


if __name__ == "__main__":
    asyncio.run(main())

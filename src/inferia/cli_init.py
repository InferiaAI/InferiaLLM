import os
import re
import asyncio
import asyncpg
import logging
from pathlib import Path
import subprocess
from dotenv import load_dotenv
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


load_dotenv()

_IDENTIFIER_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


def _safe_ident(name: str) -> str:
    if not name:
        return ""
    if not _IDENTIFIER_RE.match(name):
        raise ValueError(f"Unsafe SQL identifier: {name}")
    return name


def _require_env(name: str, allow_empty: bool = False) -> str:
    value = os.getenv(name)
    if not value and not allow_empty:
        # Special case: try to derive from DATABASE_URL if it's an app-level var
        db_url = os.getenv("DATABASE_URL")
        if db_url and name in [
            "INFERIA_DB_USER",
            "INFERIA_DB_PASSWORD",
            "INFERIA_DB",
            "PG_HOST",
            "PG_PORT",
        ]:
            try:
                parsed = urlparse(db_url)
                if name == "INFERIA_DB_USER":
                    return parsed.username or ""
                if name == "INFERIA_DB_PASSWORD":
                    return parsed.password or ""
                if name == "INFERIA_DB":
                    return parsed.path.lstrip("/")
                if name == "PG_HOST":
                    return parsed.hostname or "localhost"
                if name == "PG_PORT":
                    return str(parsed.port or 5432)
            except Exception:
                pass

        raise RuntimeError(f"Missing required environment variable: {name}")
    return value or ""


BASE_DIR = Path(__file__).parent
SCHEMA_DIR = BASE_DIR / "infra" / "schema"

# Path to API Gateway bootstrap script
API_GATEWAY_BOOTSTRAP_SCRIPT = BASE_DIR / "services" / "api_gateway" / "bootstrap_db.py"


MIGRATIONS_DIR = SCHEMA_DIR / "migrations"


async def _apply_migrations(dsn: str):
    """Apply any unapplied SQL migrations from the migrations directory.

    Supports @SPLIT@ marker to separate transactional and concurrent chunks.
    Migrations split on @SPLIT@ are executed in two phases:
    1. Transactional chunk (before @SPLIT@) - runs inside BEGIN/COMMIT
    2. Concurrent chunk (after @SPLIT@) - runs outside transaction (for CREATE INDEX CONCURRENTLY, etc.)
    """
    if not MIGRATIONS_DIR.exists():
        return

    migration_files = sorted(
        f for f in MIGRATIONS_DIR.iterdir()
        if f.suffix == ".sql" and f.name[0].isdigit()
    )
    if not migration_files:
        return

    conn = await asyncpg.connect(dsn)
    try:
        # Create tracking table if it doesn't exist
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                filename VARCHAR PRIMARY KEY,
                applied_at TIMESTAMP DEFAULT now()
            )
        """)

        applied = {
            row["filename"]
            for row in await conn.fetch("SELECT filename FROM schema_migrations")
        }

        for mf in migration_files:
            if mf.name in applied:
                continue
            print(f"[inferia:init] Applying migration: {mf.name}")

            sql_content = mf.read_text()
            chunks = sql_content.split("-- @SPLIT@")

            # First chunk (transactional)
            if chunks:
                transactional_sql = chunks[0].strip()
                if transactional_sql:
                    try:
                        async with conn.transaction():
                            await conn.execute(transactional_sql)
                    except Exception as e:
                        print(f"[inferia:init] Migration failed ({mf.name}): {e}")
                        raise

            # Subsequent chunks (concurrent, outside transaction)
            for i, concurrent_sql in enumerate(chunks[1:], start=1):
                concurrent_sql = concurrent_sql.strip()
                if concurrent_sql:
                    try:
                        print(f"[inferia:init] Applying migration {mf.name} (concurrent chunk {i})")
                        await conn.execute(concurrent_sql)
                    except Exception as e:
                        print(f"[inferia:init] Concurrent chunk {i} failed ({mf.name}): {e}")
                        raise

            # Mark migration as applied only after all chunks (transactional + concurrent) succeed
            try:
                async with conn.transaction():
                    await conn.execute(
                        "INSERT INTO schema_migrations (filename) VALUES ($1)",
                        mf.name,
                    )
            except Exception as e:
                print(f"[inferia:init] Failed to record migration ({mf.name}): {e}")
                raise
    finally:
        await conn.close()


async def _execute_schema(dsn: str, sql_file: Path, label: str):
    if not sql_file.exists():
        print(f"[inferia:init] Skipping schema (not found): {label}")
        return

    print(f"[inferia:init] Applying schema: {label}")
    conn = await asyncpg.connect(dsn)

    try:
        await conn.execute("BEGIN")
        await conn.execute(sql_file.read_text())
        await conn.execute("COMMIT")

    except (
        asyncpg.DuplicateObjectError,
        asyncpg.DuplicateTableError,
        asyncpg.DuplicateFunctionError,
        asyncpg.DuplicatePreparedStatementError,
        asyncpg.UniqueViolationError,
    ) as e:
        # Idempotent behavior: schema already applied
        print(
            f"[inferia:init] Schema already initialized, skipping ({label}): "
            f"{e.__class__.__name__}"
        )

    finally:
        await conn.close()


def _bootstrap_api_gateway(database_url: str):
    if not API_GATEWAY_BOOTSTRAP_SCRIPT.exists():
        raise RuntimeError(
            f"API Gateway bootstrap script not found: {API_GATEWAY_BOOTSTRAP_SCRIPT}"
        )

    print(
        "[inferia:init] Bootstrapping API Gateway database (tables, default org, super admin)"
    )

    # --------------------------------------------------
    # Minimal, api_gateway-scoped environment ONLY
    # --------------------------------------------------
    clean_env = {
        # Required runtime basics
        "PYTHONPATH": os.getenv("PYTHONPATH", ""),
        "PATH": os.getenv("PATH", ""),
        "VIRTUAL_ENV": os.getenv("VIRTUAL_ENV", ""),
        # API Gateway DB - Explicitly passed
        "DATABASE_URL": database_url,
        # Envs for Super Admin creation (passed from current env)
        "SUPERADMIN_EMAIL": os.getenv("SUPERADMIN_EMAIL", ""),
        "SUPERADMIN_PASSWORD": os.getenv("SUPERADMIN_PASSWORD", ""),
        "DEFAULT_ORG_NAME": os.getenv("DEFAULT_ORG_NAME", ""),
        # Security/Auth secrets required by API Gateway Config
        "INTERNAL_API_KEY": os.getenv("INTERNAL_API_KEY", ""),
        "JWT_SECRET_KEY": os.getenv("JWT_SECRET_KEY", ""),
        # We also need these if config.py requires them to validate settings
        # although defaults are set in config.py now.
        # Optional: logging / runtime
        "ENV": os.getenv("ENV", "local"),
    }

    # Remove empty values
    clean_env = {k: v for k, v in clean_env.items() if v}

    subprocess.run(
        ["python3", str(API_GATEWAY_BOOTSTRAP_SCRIPT)],
        check=True,
        env=clean_env,
    )


async def _init():
    admin_user = _require_env("PG_ADMIN_USER")
    admin_password = _require_env("PG_ADMIN_PASSWORD", allow_empty=True)

    inferia_user = _safe_ident(_require_env("INFERIA_DB_USER"))
    inferia_password = _require_env("INFERIA_DB_PASSWORD")

    pg_host = _require_env("PG_HOST")
    pg_port = _require_env("PG_PORT")

    # Use _require_env to allow derivation from DATABASE_URL
    inferia_db = _safe_ident(_require_env("INFERIA_DB", allow_empty=True) or "inferia")

    admin_dsn = (
        f"postgresql://{admin_user}:{admin_password}@{pg_host}:{pg_port}/template1"
    )

    print(f"[inferia:init] Connecting as admin to bootstrap {inferia_db}")
    conn = await asyncpg.connect(admin_dsn)

    try:
        # --------------------------------------------------
        # Create inferia role
        # --------------------------------------------------
        role_exists = await conn.fetchval(
            "SELECT 1 FROM pg_roles WHERE rolname = $1",
            inferia_user,
        )

        if not role_exists:
            print(f"[inferia:init] Creating role: {inferia_user}")
            # Use quote_literal() to safely escape the password and prevent
            # SQL injection from passwords containing single quotes.
            escaped_pw = await conn.fetchval(
                "SELECT quote_literal($1)", inferia_password
            )
            await conn.execute(
                f"CREATE ROLE {inferia_user} LOGIN PASSWORD {escaped_pw}"
            )
        else:
            print(f"[inferia:init] Role exists: {inferia_user}")

        # --------------------------------------------------
        # Create database
        # --------------------------------------------------
        db_exists = await conn.fetchval(
            "SELECT 1 FROM pg_database WHERE datname = $1",
            inferia_db,
        )

        if not db_exists:
            print(f"[inferia:init] Creating database: {inferia_db}")
            await conn.execute(f'CREATE DATABASE "{inferia_db}" OWNER {inferia_user}')
        else:
            print(f"[inferia:init] Database exists: {inferia_db}")

    finally:
        await conn.close()

    # --------------------------------------------------
    # Fix schema ownership + privileges
    # --------------------------------------------------
    print(f"[inferia:init] Repairing privileges on {inferia_db}")
    db_dsn = (
        f"postgresql://{admin_user}:{admin_password}@{pg_host}:{pg_port}/{inferia_db}"
    )

    conn = await asyncpg.connect(db_dsn)
    try:
        await conn.execute(
            f"""
            ALTER SCHEMA public OWNER TO {inferia_user};
            GRANT ALL ON SCHEMA public TO {inferia_user};
            GRANT ALL ON ALL TABLES IN SCHEMA public TO {inferia_user};
            GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO {inferia_user};

            ALTER DEFAULT PRIVILEGES IN SCHEMA public
            GRANT ALL ON TABLES TO {inferia_user};

            ALTER DEFAULT PRIVILEGES IN SCHEMA public
            GRANT ALL ON SEQUENCES TO {inferia_user};
            """
        )
    finally:
        await conn.close()

    # --------------------------------------------------
    # Apply schemas as inferia
    # --------------------------------------------------
    inferia_dsn = (
        f"postgresql://{inferia_user}:{inferia_password}"
        f"@{pg_host}:{pg_port}/{inferia_db}"
    )

    await _execute_schema(
        inferia_dsn,
        SCHEMA_DIR / "global_schema.sql",
        "global_schema",
    )

    # --------------------------------------------------
    # Apply incremental migrations
    # --------------------------------------------------
    await _apply_migrations(inferia_dsn)

    # --------------------------------------------------
    # Application-level bootstrap (API Gateway only)
    # --------------------------------------------------

    # Construct DSN for SQLAlchemy
    api_gateway_dsn_alchemy = (
        f"postgresql+asyncpg://{inferia_user}:{inferia_password}"
        f"@{pg_host}:{pg_port}/{inferia_db}"
    )

    _bootstrap_api_gateway(api_gateway_dsn_alchemy)

    print("\n[inferia:init] Bootstrap complete")


def init_databases():
    asyncio.run(_init())


async def run_migrations():
    """Standalone migration runner for existing installations."""
    inferia_user = _safe_ident(_require_env("INFERIA_DB_USER"))
    inferia_password = _require_env("INFERIA_DB_PASSWORD")
    pg_host = _require_env("PG_HOST")
    pg_port = _require_env("PG_PORT")
    inferia_db = _safe_ident(_require_env("INFERIA_DB", allow_empty=True) or "inferia")

    dsn = (
        f"postgresql://{inferia_user}:{inferia_password}"
        f"@{pg_host}:{pg_port}/{inferia_db}"
    )
    await _apply_migrations(dsn)
    print("[inferia:migrate] Done")

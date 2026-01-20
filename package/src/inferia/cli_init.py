import os
import re
import asyncio
import asyncpg
from pathlib import Path
import subprocess   # NEW
from dotenv import load_dotenv


load_dotenv()

_IDENTIFIER_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


def _safe_ident(name: str) -> str:
    if not _IDENTIFIER_RE.match(name):
        raise ValueError(f"Unsafe SQL identifier: {name}")
    return name


def _require_env(name: str, allow_empty: bool = False) -> str:
    value = os.getenv(name)
    if not value and not allow_empty:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value or ""


BASE_DIR = Path(__file__).parent
SCHEMA_DIR = BASE_DIR / "infra" / "schema"

# Path to filtration bootstrap script
FILTRATION_BOOTSTRAP_SCRIPT = BASE_DIR / "services" / "filtration" / "bootstrap_db.py"


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



def _bootstrap_filtration(database_url: str):
    if not FILTRATION_BOOTSTRAP_SCRIPT.exists():
        raise RuntimeError(
            f"Filtration bootstrap script not found: {FILTRATION_BOOTSTRAP_SCRIPT}"
        )

    print("[inferia:init] Bootstrapping filtration database (tables, default org, super admin)")

    # --------------------------------------------------
    # Minimal, filtration-scoped environment ONLY
    # --------------------------------------------------
    clean_env = {
        # Required runtime basics
        "PYTHONPATH": os.getenv("PYTHONPATH", ""),
        "PATH": os.getenv("PATH", ""),
        "VIRTUAL_ENV": os.getenv("VIRTUAL_ENV", ""),

        # Filtration DB - Explicitly passed
        "DATABASE_URL": database_url,
        
        # Envs for Super Admin creation (passed from current env)
        "SUPERADMIN_EMAIL": os.getenv("SUPERADMIN_EMAIL", ""),
        "SUPERADMIN_PASSWORD": os.getenv("SUPERADMIN_PASSWORD", ""),
        "DEFAULT_ORG_NAME": os.getenv("DEFAULT_ORG_NAME", ""),
        
        # Security/Auth secrets required by Filtration Config
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
        ["python3", str(FILTRATION_BOOTSTRAP_SCRIPT)],
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

    orchestration_db = _safe_ident(_require_env("ORCHESTRATION_DB"))
    filtration_db = _safe_ident(_require_env("FILTRATION_DB"))

    admin_dsn = (
        f"postgresql://{admin_user}:{admin_password}"
        f"@{pg_host}:{pg_port}/template1"
    )

    print("[inferia:init] Connecting as admin")
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
            await conn.execute(
                f"""
                CREATE ROLE {inferia_user}
                LOGIN
                PASSWORD '{inferia_password}'
                """
            )
        else:
            print(f"[inferia:init] Role exists: {inferia_user}")

        # --------------------------------------------------
        # Create databases
        # --------------------------------------------------
        existing_dbs = {
            r["datname"]
            for r in await conn.fetch(
                "SELECT datname FROM pg_database WHERE datistemplate = false"
            )
        }

        for db in (orchestration_db,):
            if db not in existing_dbs:
                print(f"[inferia:init] Creating database: {db}")
                await conn.execute(
                    f'CREATE DATABASE "{db}" OWNER {inferia_user}'
                )
            else:
                print(f"[inferia:init] Database exists: {db}")

    finally:
        await conn.close()

    # --------------------------------------------------
    # Fix schema ownership + privileges
    # --------------------------------------------------
    for db in (orchestration_db, filtration_db):
        print(f"[inferia:init] Repairing privileges on {db}")
        db_dsn = (
            f"postgresql://{admin_user}:{admin_password}"
            f"@{pg_host}:{pg_port}/{db}"
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
    inferia_dsn_tpl = (
        f"postgresql://{inferia_user}:{inferia_password}"
        f"@{pg_host}:{pg_port}/{{db}}"
    )

    await _execute_schema(
        inferia_dsn_tpl.format(db=orchestration_db),
        SCHEMA_DIR / "global_schema.sql",
        "global_schema",
    )


    # --------------------------------------------------
    # Application-level bootstrap (filtration only)
    # --------------------------------------------------
    
    # Construct DSN for SQLAlchemy (needs async driver scheme usually, or relies on config)
    # create_async_engine handles 'postgresql+asyncpg://' best.
    filtration_dsn_alchemy = (
        f"postgresql+asyncpg://{inferia_user}:{inferia_password}"
        f"@{pg_host}:{pg_port}/{filtration_db}"
    )
    
    _bootstrap_filtration(filtration_dsn_alchemy)

    print("\n[inferia:init] Bootstrap complete")


def init_databases():
    asyncio.run(_init())

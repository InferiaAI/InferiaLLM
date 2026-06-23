"""DB-backed provider credential management for the `inferiallm providers` CLI.

Reads and writes the same `system_settings` JSON blob that the dashboard uses
(key = "providers_config").  All operations are async, backed by asyncpg.

Persistence note: the dashboard writes/reads via SQLAlchemy with the
`EncryptedJSON` type decorator at `api_gateway.db.security` —
the on-disk value is `{"data": "<Fernet-encrypted JSON>"}`. This CLI mirrors
that contract so the dashboard and CLI see the same view of the data.

Supported providers: aws, gcp, azure, ibm, nosana.

Storage shape (nested, matching api_gateway ProvidersConfig):
  {
    "cloud":  {"aws": {...}, "gcp": {...}, "azure": {...}, "ibm": {...}},
    "vectordb": {"chroma": {...}},
    "depin": {"nosana": {"wallet_private_key": ..., "api_keys": [...]}}
  }
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any, Optional

_CONFIG_KEY = "providers_config"
_CLOUD_PROVIDERS = {"aws", "gcp", "azure", "ibm"}
_ALL_PROVIDERS = _CLOUD_PROVIDERS | {"nosana"}


def _fernet():
    """Return a Fernet instance built from SECRET_ENCRYPTION_KEY, or None.

    Matches the dashboard's encryption layer in
    `api_gateway.db.security.EncryptionService`. When no key
    is configured (dev mode) the dashboard stores plain JSON; we mirror that.
    """
    key = os.environ.get("SECRET_ENCRYPTION_KEY")
    if not key:
        return None
    try:
        from cryptography.fernet import Fernet

        return Fernet(key.encode())
    except Exception as e:  # pragma: no cover — invalid key
        print(f"[providers] warning: SECRET_ENCRYPTION_KEY rejected by Fernet: {e}", file=sys.stderr)
        return None


def _decrypt_blob(raw: Any) -> dict:
    """Decode the on-disk blob into the plain provider config dict.

    The dashboard's `EncryptedJSON.process_result_value` does the equivalent:
    if the row is `{"data": "<ciphertext>"}`, decrypt and json.loads.
    Otherwise the row is plain JSON — return it as-is.
    """
    if raw is None:
        return {}
    if isinstance(raw, str):
        raw = json.loads(raw)
    if not isinstance(raw, dict):
        return {}
    # Encrypted shape: {"data": "<base64 ciphertext>"}
    if set(raw.keys()) == {"data"} and isinstance(raw["data"], str):
        f = _fernet()
        if f is None:
            return {}
        try:
            plaintext = f.decrypt(raw["data"].encode()).decode()
            return json.loads(plaintext)
        except Exception as e:
            print(f"[providers] warning: failed to decrypt providers blob: {e}", file=sys.stderr)
            return {}
    # Plain JSON shape (dev mode without encryption key)
    return raw


def _encrypt_blob(cfg: dict) -> dict:
    """Encode the provider config dict into the on-disk blob shape.

    Matches `EncryptedJSON.process_bind_param`: produce `{"data": "<ciphertext>"}`
    when an encryption key is configured, else the plain JSON dict.
    """
    f = _fernet()
    if f is None:
        return cfg
    ciphertext = f.encrypt(json.dumps(cfg).encode()).decode()
    return {"data": ciphertext}


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _build_dsn() -> str:
    """Build an asyncpg-compatible DSN from environment variables."""
    db_url = os.getenv("DATABASE_URL", "")
    if db_url:
        # Strip asyncpg driver prefix if present
        dsn = db_url.replace("postgresql+asyncpg://", "postgresql://", 1)
        return dsn
    user = os.getenv("INFERIA_DB_USER", "inferia")
    password = os.getenv("INFERIA_DB_PASSWORD", "inferia")
    host = os.getenv("PG_HOST", "localhost")
    port = os.getenv("PG_PORT", "5432")
    db = os.getenv("INFERIA_DB", "inferia")
    return f"postgresql://{user}:{password}@{host}:{port}/{db}"


async def _read_config(conn) -> dict:
    """Read the providers blob from system_settings.  Returns {} if missing.

    Transparently decrypts the EncryptedJSON wrapper so the rest of the CLI
    sees the same plain dict shape the dashboard does.
    """
    row = await conn.fetchrow(
        "SELECT value FROM system_settings WHERE key = $1", _CONFIG_KEY
    )
    if row is None:
        return {}
    cfg = _decrypt_blob(row["value"])
    # The dashboard sometimes stores the full ConfigUpdate shape as
    # {"providers": {...}} and sometimes the bare providers dict. Normalise.
    if isinstance(cfg, dict) and "providers" in cfg and len(cfg) == 1:
        cfg = cfg["providers"]
    return cfg if isinstance(cfg, dict) else {}


async def _write_config(conn, cfg: dict) -> None:
    """Upsert the providers blob into system_settings, matching dashboard encryption.

    The dashboard's `save_config` stores `{"providers": {...}}` wrapped — keep
    the same shape so the dashboard's read path sees a consistent view.
    """
    wrapped = {"providers": cfg}
    blob = _encrypt_blob(wrapped)
    await conn.execute(
        """
        INSERT INTO system_settings (key, value)
        VALUES ($1, $2::jsonb)
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
        """,
        _CONFIG_KEY,
        json.dumps(blob),
    )


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------

def _get_cloud_cfg(cfg: dict, provider: str) -> dict:
    return cfg.setdefault("cloud", {}).setdefault(provider, {})


def _get_nosana_cfg(cfg: dict) -> dict:
    depin = cfg.setdefault("depin", {})
    nosana = depin.setdefault("nosana", {})
    nosana.setdefault("api_keys", [])
    return nosana


async def cmd_list(conn, provider: Optional[str]) -> None:
    """Print a table of provider credentials (no secret values)."""
    cfg = await _read_config(conn)

    rows: list[tuple[str, str, str, bool]] = []  # (provider, name, type, active)

    providers_to_show = [provider] if provider else sorted(_ALL_PROVIDERS)
    for prov in providers_to_show:
        if prov in _CLOUD_PROVIDERS:
            cloud_prov = cfg.get("cloud", {}).get(prov, {})
            for field_name, val in cloud_prov.items():
                if isinstance(val, str) and val:
                    rows.append((prov, "default", field_name, True))
        elif prov == "nosana":
            nosana = cfg.get("depin", {}).get("nosana", {})
            wpk = nosana.get("wallet_private_key")
            if wpk:
                rows.append(("nosana", "wallet", "wallet_private_key", True))
            for entry in nosana.get("api_keys", []):
                if isinstance(entry, dict) and entry.get("key"):
                    rows.append((
                        "nosana",
                        entry.get("name", "?"),
                        "api_key",
                        bool(entry.get("is_active", True)),
                    ))

    if not rows:
        print("No credentials found.")
        return

    header = f"{'PROVIDER':<12}{'NAME':<20}{'TYPE':<30}{'ACTIVE'}"
    print(header)
    print("-" * len(header))
    for prov, name, ctype, active in rows:
        print(f"{prov:<12}{name:<20}{ctype:<30}{active}")


async def cmd_add(conn, provider: str, name: str, ctype: str, value: str, active: bool) -> None:
    """Add or overwrite a credential field."""
    cfg = await _read_config(conn)

    if provider in _CLOUD_PROVIDERS:
        prov_cfg = _get_cloud_cfg(cfg, provider)
        prov_cfg[ctype] = value
        print(f"Set {provider}.{ctype} (name={name!r} is informational for cloud providers).")
    elif provider == "nosana":
        nosana = _get_nosana_cfg(cfg)
        if ctype == "wallet_private_key":
            nosana["wallet_private_key"] = value
            print("Set nosana.wallet_private_key.")
        else:
            # api_key entry by name
            existing = next(
                (e for e in nosana["api_keys"] if isinstance(e, dict) and e.get("name") == name),
                None,
            )
            if existing is not None:
                print(f"Key '{name}' already exists. Use 'update' to change it.", file=sys.stderr)
                sys.exit(1)
            nosana["api_keys"].append({"name": name, "key": value, "is_active": active})
            print(f"Added nosana api_key '{name}'.")

    await _write_config(conn, cfg)


async def cmd_update(conn, provider: str, name: str, ctype: Optional[str], value: Optional[str], active: Optional[bool]) -> None:
    """Update an existing credential."""
    cfg = await _read_config(conn)

    if provider in _CLOUD_PROVIDERS:
        if not ctype:
            print("--type is required for cloud providers.", file=sys.stderr)
            sys.exit(1)
        prov_cfg = _get_cloud_cfg(cfg, provider)
        if ctype not in prov_cfg:
            print(f"{provider}.{ctype} not found.", file=sys.stderr)
            sys.exit(1)
        if value is not None:
            prov_cfg[ctype] = value
            print(f"Updated {provider}.{ctype}.")
    elif provider == "nosana":
        nosana = _get_nosana_cfg(cfg)
        if name == "wallet" or (ctype and ctype == "wallet_private_key"):
            if value is not None:
                nosana["wallet_private_key"] = value
                print("Updated nosana.wallet_private_key.")
        else:
            entry = next(
                (e for e in nosana["api_keys"] if isinstance(e, dict) and e.get("name") == name),
                None,
            )
            if entry is None:
                print(f"Nosana key '{name}' not found.", file=sys.stderr)
                sys.exit(1)
            if value is not None:
                entry["key"] = value
            if active is not None:
                entry["is_active"] = active
            print(f"Updated nosana api_key '{name}'.")

    await _write_config(conn, cfg)


async def cmd_remove(conn, provider: str, name: str) -> None:
    """Remove a credential."""
    cfg = await _read_config(conn)

    if provider in _CLOUD_PROVIDERS:
        prov_cfg = cfg.get("cloud", {}).get(provider, {})
        if name not in prov_cfg:
            print(f"{provider}.{name} not found.", file=sys.stderr)
            sys.exit(1)
        del prov_cfg[name]
        print(f"Removed {provider}.{name}.")
    elif provider == "nosana":
        nosana = cfg.get("depin", {}).get("nosana", {})
        if name == "wallet":
            if nosana.get("wallet_private_key") is None:
                print("nosana.wallet_private_key not set.", file=sys.stderr)
                sys.exit(1)
            nosana["wallet_private_key"] = None
            print("Cleared nosana.wallet_private_key.")
        else:
            keys = nosana.get("api_keys", [])
            new_keys = [e for e in keys if not (isinstance(e, dict) and e.get("name") == name)]
            if len(new_keys) == len(keys):
                print(f"Nosana key '{name}' not found.", file=sys.stderr)
                sys.exit(1)
            nosana["api_keys"] = new_keys
            print(f"Removed nosana api_key '{name}'.")

    await _write_config(conn, cfg)


# ---------------------------------------------------------------------------
# Entry point (called from cli.py)
# ---------------------------------------------------------------------------

def run_providers_command(args) -> None:
    """Dispatch the `inferiallm providers` sub-command."""
    import asyncpg

    provider = getattr(args, "provider", None)

    if provider and provider not in _ALL_PROVIDERS:
        print(
            f"Unknown provider '{provider}'. Valid: {', '.join(sorted(_ALL_PROVIDERS))}",
            file=sys.stderr,
        )
        sys.exit(1)

    async def _main():
        dsn = _build_dsn()
        conn = await asyncpg.connect(dsn)
        try:
            action = args.providers_action
            if action == "list":
                await cmd_list(conn, provider)
            elif action == "add":
                active = _parse_bool(getattr(args, "active", "true"))
                await cmd_add(conn, provider, args.name, args.type, args.value, active)
            elif action == "update":
                active = _parse_bool_opt(getattr(args, "active", None))
                await cmd_update(conn, provider, args.name, getattr(args, "type", None), getattr(args, "value", None), active)
            elif action == "remove":
                await cmd_remove(conn, provider, args.name)
            else:
                print(f"Unknown action: {action}", file=sys.stderr)
                sys.exit(1)
        finally:
            await conn.close()

    asyncio.run(_main())


def _parse_bool(v: str) -> bool:
    return str(v).lower() not in ("false", "0", "no", "n")


def _parse_bool_opt(v: Optional[str]) -> Optional[bool]:
    if v is None:
        return None
    return _parse_bool(v)

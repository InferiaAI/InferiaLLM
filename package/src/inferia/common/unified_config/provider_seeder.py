"""Sync public.provider_credentials with the yaml providers section.

This module is intentionally self-contained: it imports only from the standard
library, asyncpg, cryptography.fernet, and the unified_config package itself.
It does NOT import anything from inferia.services.* to avoid circular deps.

Public API
----------
seed_providers_from_yaml(dsn, encryption_key, cfg=None) -> SeedReport
    Synchronise the DB table to match yaml.providers (upsert + delete).

_extract_rows(providers_obj) -> list[ProviderRow]
    Pure function: turn an InferiaConfig.providers value into row tuples.
    Exposed for unit testing; callers outside this module should prefer the
    full async entrypoint.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class ProviderRow:
    """One row to upsert into provider_credentials."""

    provider: str
    name: str
    credential_type: str
    value: str  # plaintext; encrypted before writing to DB
    is_active: bool = True


@dataclass
class SeedReport:
    """Result of a single seed_providers_from_yaml run."""

    skipped: bool = False
    reason: str = ""
    inserted: int = 0
    updated: int = 0
    deleted: int = 0


# ---------------------------------------------------------------------------
# Pure-function: yaml providers object → row list
# ---------------------------------------------------------------------------


def _nonempty(val: Any) -> bool:
    """Return True if *val* is a non-empty, non-whitespace string."""
    return isinstance(val, str) and bool(val.strip())


def _extract_rows(providers_obj: Any) -> list[ProviderRow]:
    """Convert the providers section of InferiaConfig into ProviderRow list.

    The mapping is:
      providers.cloud.aws.access_key_id         → (aws, default, access_key_id)
      providers.cloud.aws.secret_access_key     → (aws, default, secret_access_key)
      providers.cloud.gcp.service_account_json  → (gcp, default, service_account_json)
      providers.vectordb.chroma.api_key         → (chroma, default, api_key)
      providers.vectordb.chroma.tenant          → (chroma, default, tenant)
      providers.vectordb.chroma.url             → (chroma, default, url)
      providers.vectordb.chroma.database        → (chroma, default, database)
      providers.guardrails.groq.api_key         → (groq, default, api_key)
      providers.guardrails.lakera.api_key       → (lakera, default, api_key)
      providers.depin.nosana.wallet_private_key → (nosana, wallet, wallet_private_key)
      providers.depin.nosana.api_keys[i].key    → (nosana, api_keys[i].name, api_key)
      providers.depin.akash.mnemonic            → (akash, default, mnemonic)

    Empty / null / whitespace-only values are skipped.
    """
    if providers_obj is None:
        return []

    # providers_obj is a Pydantic model with extra="allow".
    # Convert to plain dict for uniform traversal.
    if hasattr(providers_obj, "model_dump"):
        data: dict = providers_obj.model_dump()
    elif isinstance(providers_obj, dict):
        data = providers_obj
    else:
        return []

    rows: list[ProviderRow] = []

    # ── cloud ──────────────────────────────────────────────────────────────
    cloud = data.get("cloud") or {}

    # AWS
    aws = cloud.get("aws") or {}
    _maybe_row(rows, aws, "access_key_id", "aws", "default", "access_key_id")
    _maybe_row(rows, aws, "secret_access_key", "aws", "default", "secret_access_key")

    # GCP
    gcp = cloud.get("gcp") or {}
    _maybe_row(rows, gcp, "service_account_json", "gcp", "default", "service_account_json")

    # ── vectordb ───────────────────────────────────────────────────────────
    vectordb = data.get("vectordb") or {}

    chroma = vectordb.get("chroma") or {}
    _maybe_row(rows, chroma, "api_key", "chroma", "default", "api_key")
    _maybe_row(rows, chroma, "tenant", "chroma", "default", "tenant")
    _maybe_row(rows, chroma, "url", "chroma", "default", "url")
    _maybe_row(rows, chroma, "database", "chroma", "default", "database")

    # ── guardrails ─────────────────────────────────────────────────────────
    guardrails = data.get("guardrails") or {}

    groq = guardrails.get("groq") or {}
    _maybe_row(rows, groq, "api_key", "groq", "default", "api_key")

    lakera = guardrails.get("lakera") or {}
    _maybe_row(rows, lakera, "api_key", "lakera", "default", "api_key")

    # ── depin ──────────────────────────────────────────────────────────────
    depin = data.get("depin") or {}

    nosana = depin.get("nosana") or {}
    _maybe_row(rows, nosana, "wallet_private_key", "nosana", "wallet", "wallet_private_key")

    # nosana.api_keys is a list of {name, key, is_active?} entries
    api_keys_list = nosana.get("api_keys") or []
    if isinstance(api_keys_list, list):
        for entry in api_keys_list:
            if not isinstance(entry, dict):
                continue
            key_name = entry.get("name")
            key_value = entry.get("key")
            if not _nonempty(key_name) or not _nonempty(key_value):
                continue
            is_active = bool(entry.get("is_active", True))
            rows.append(
                ProviderRow(
                    provider="nosana",
                    name=str(key_name).strip(),
                    credential_type="api_key",
                    value=str(key_value).strip(),
                    is_active=is_active,
                )
            )

    akash = depin.get("akash") or {}
    _maybe_row(rows, akash, "mnemonic", "akash", "default", "mnemonic")

    return rows


def _maybe_row(
    rows: list[ProviderRow],
    source: dict,
    field_key: str,
    provider: str,
    name: str,
    credential_type: str,
    is_active: bool = True,
) -> None:
    """Append a ProviderRow to *rows* if *source[field_key]* is non-empty."""
    val = source.get(field_key)
    if _nonempty(val):
        rows.append(
            ProviderRow(
                provider=provider,
                name=name,
                credential_type=credential_type,
                value=str(val).strip(),
                is_active=is_active,
            )
        )


# ---------------------------------------------------------------------------
# Encryption helper (self-contained; does NOT import api_gateway.db.security)
# ---------------------------------------------------------------------------


def _make_fernet(key: str):
    """Return a Fernet instance, or None if the key is invalid.

    Validates that *key* is a properly formatted Fernet key (URL-safe base64,
    32 bytes when decoded) before constructing, so callers get a clear error
    rather than a cryptic binascii exception later.
    """
    from cryptography.fernet import Fernet, InvalidToken  # noqa: F401

    try:
        # Fernet.__init__ raises ValueError for badly-formatted keys.
        return Fernet(key.encode() if isinstance(key, str) else key)
    except Exception as exc:
        logger.warning(
            "provider_seeder: invalid Fernet key — seeder will skip. "
            "SECRET_ENCRYPTION_KEY must be a valid Fernet key "
            "(URL-safe base64-encoded 32 bytes). "
            "Generate one with: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\" "
            "Error: %s",
            exc,
        )
        return None


def _encrypt(fernet, plaintext: str) -> str:
    """Encrypt *plaintext* using *fernet*. Returns the ciphertext string."""
    return fernet.encrypt(plaintext.encode()).decode()


# ---------------------------------------------------------------------------
# Config loader shim (module-level so tests can monkeypatch it)
# ---------------------------------------------------------------------------


def _load_config():
    """Load the unified config. Extracted at module scope for test patchability."""
    from inferia.common.unified_config.loader import load_unified_config  # noqa: PLC0415

    return load_unified_config()


# ---------------------------------------------------------------------------
# DB sync (async, asyncpg)
# ---------------------------------------------------------------------------


async def seed_providers_from_yaml(
    dsn: str,
    encryption_key: Optional[str],
    cfg=None,  # InferiaConfig | None — typed as Any to avoid re-import overhead
) -> SeedReport:
    """Sync DB.provider_credentials to match yaml.providers.

    Parameters
    ----------
    dsn:
        asyncpg-compatible DSN (postgresql://…).
    encryption_key:
        Fernet key for credential_value_encrypted. If None, the seeder logs a
        warning and returns SeedReport(skipped=True).
    cfg:
        Optional pre-loaded InferiaConfig. If None, calls load_unified_config().
        If that returns None (no yaml found), the seeder skips.

    Returns
    -------
    SeedReport with counts of inserted, updated, and deleted rows.
    """
    import asyncpg  # import inside function to keep module importable w/o asyncpg

    # ── Step 1: validate encryption key ────────────────────────────────────
    if not encryption_key:
        logger.info(
            "provider_seeder: SECRET_ENCRYPTION_KEY is unset — skipping provider sync"
        )
        return SeedReport(skipped=True, reason="encryption_key is unset")

    fernet = _make_fernet(encryption_key)
    if fernet is None:
        return SeedReport(skipped=True, reason="invalid encryption key")

    # ── Step 2: load yaml config ────────────────────────────────────────────
    if cfg is None:
        cfg = _load_config()

    if cfg is None:
        logger.info(
            "provider_seeder: no unified config yaml found — skipping provider sync"
        )
        return SeedReport(skipped=True, reason="no yaml config found")

    providers_obj = getattr(cfg, "providers", None)
    if providers_obj is None:
        logger.info(
            "provider_seeder: yaml has no providers section — skipping provider sync"
        )
        return SeedReport(skipped=True, reason="yaml has no providers section")

    # ── Step 3: extract desired rows from yaml ──────────────────────────────
    desired_rows = _extract_rows(providers_obj)

    # Build desired set: {(provider, name)} for the delete query
    desired_keys = {(r.provider, r.name) for r in desired_rows}

    logger.info(
        "provider_seeder: %d credential row(s) extracted from yaml",
        len(desired_rows),
    )

    # ── Step 4: sync to DB in a single transaction ──────────────────────────
    conn = await asyncpg.connect(dsn)
    inserted = 0
    updated = 0
    deleted = 0

    try:
        async with conn.transaction():
            # --- Delete rows not in desired set ----------------------------
            # Fetch existing (provider, name) pairs first.
            existing = await conn.fetch(
                "SELECT provider, name FROM provider_credentials"
            )
            to_delete = [
                (row["provider"], row["name"])
                for row in existing
                if (row["provider"], row["name"]) not in desired_keys
            ]

            for prov, nm in to_delete:
                await conn.execute(
                    "DELETE FROM provider_credentials WHERE provider = $1 AND name = $2",
                    prov,
                    nm,
                )
                deleted += 1
                logger.debug(
                    "provider_seeder: deleted (%s, %s) — not in yaml", prov, nm
                )

            # --- Upsert desired rows ----------------------------------------
            existing_keys = {(row["provider"], row["name"]) for row in existing}

            for row in desired_rows:
                encrypted_value = _encrypt(fernet, row.value)

                result = await conn.execute(
                    """
                    INSERT INTO provider_credentials
                        (provider, name, credential_type, credential_value_encrypted, is_active)
                    VALUES ($1, $2, $3, $4, $5)
                    ON CONFLICT (provider, name) DO UPDATE
                        SET credential_value_encrypted = EXCLUDED.credential_value_encrypted,
                            credential_type            = EXCLUDED.credential_type,
                            is_active                  = EXCLUDED.is_active,
                            updated_at                 = now()
                    """,
                    row.provider,
                    row.name,
                    row.credential_type,
                    encrypted_value,
                    row.is_active,
                )

                # asyncpg returns "INSERT 0 1" or "UPDATE 1" as a string tag
                if (row.provider, row.name) in existing_keys:
                    updated += 1
                else:
                    inserted += 1

    finally:
        await conn.close()

    report = SeedReport(
        skipped=False,
        reason="",
        inserted=inserted,
        updated=updated,
        deleted=deleted,
    )
    logger.info(
        "provider_seeder: sync complete — inserted=%d updated=%d deleted=%d",
        inserted,
        updated,
        deleted,
    )
    return report

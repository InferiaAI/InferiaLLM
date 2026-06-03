"""ModelCacheRepo — asyncpg-backed repository for the model_cache table."""
from __future__ import annotations

from uuid import UUID


class ModelCacheRepo:
    """Repository for `model_cache` rows.

    Parameters
    ----------
    db:
        An asyncpg connection pool.  ``pool.acquire()`` must be usable as an
        async context manager that yields a connection.
    """

    def __init__(self, db):
        self.db = db  # asyncpg pool — supports `async with self.db.acquire() as conn`

    # ------------------------------------------------------------------
    # Write helpers
    # ------------------------------------------------------------------

    async def upsert(self, *, source: str, model_id: str, revision: str = "main", engine_hint: str | None = None) -> dict:
        """Insert or (on conflict) touch ``updated_at``; return the row.

        Note: ``ON CONFLICT DO UPDATE SET updated_at=now()`` does NOT refresh
        ``engine_hint`` — the first caller to register a ``(source, model_id,
        revision)`` triplet owns ``engine_hint`` until the row is deleted.
        """
        async with self.db.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO model_cache (source, model_id, revision, engine_hint)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (source, model_id, revision)
                DO UPDATE SET updated_at = now()
                RETURNING *
                """,
                source, model_id, revision, engine_hint,
            )
            if row is None:
                raise RuntimeError("model_cache upsert returned no row")
            return dict(row)

    async def set_progress(
        self,
        cache_id,
        *,
        bytes_total: int | None = None,
        bytes_done: int | None = None,
        status: str | None = None,
    ) -> None:
        """Partially update progress fields; ``None`` values are left unchanged."""
        async with self.db.acquire() as conn:
            await conn.execute(
                """
                UPDATE model_cache SET
                  bytes_total = COALESCE($2, bytes_total),
                  bytes_done  = COALESCE($3, bytes_done),
                  status      = COALESCE($4, status),
                  updated_at  = now()
                WHERE id = $1
                """,
                UUID(str(cache_id)), bytes_total, bytes_done, status,
            )

    async def set_status(self, cache_id, status: str, error: str | None = None) -> None:
        """Set ``status`` (and optional ``error``) on a cache row."""
        async with self.db.acquire() as conn:
            await conn.execute(
                "UPDATE model_cache SET status=$2, error=$3, updated_at=now() WHERE id=$1",
                UUID(str(cache_id)), status, error,
            )

    async def touch(self, cache_id) -> None:
        """Refresh ``last_used_at`` to now (record access)."""
        async with self.db.acquire() as conn:
            await conn.execute(
                "UPDATE model_cache SET last_used_at=now() WHERE id=$1",
                UUID(str(cache_id)),
            )

    async def touch_by_key(self, *, source: str, model_id: str, revision: str = "main") -> None:
        """Refresh ``last_used_at`` by natural key."""
        async with self.db.acquire() as conn:
            await conn.execute(
                "UPDATE model_cache SET last_used_at=now() WHERE source=$1 AND model_id=$2 AND revision=$3",
                source, model_id, revision,
            )

    async def delete(self, cache_id) -> None:
        """Hard-delete a cache row."""
        async with self.db.acquire() as conn:
            await conn.execute(
                "DELETE FROM model_cache WHERE id=$1",
                UUID(str(cache_id)),
            )

    # ------------------------------------------------------------------
    # Read helpers
    # ------------------------------------------------------------------

    async def get(self, cache_id) -> dict | None:
        """Fetch a single row by primary key; returns ``None`` if not found."""
        async with self.db.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM model_cache WHERE id=$1",
                UUID(str(cache_id)),
            )
            return dict(row) if row else None

    async def get_by_key(self, *, source: str, model_id: str, revision: str = "main") -> dict | None:
        """Fetch a single row by the natural unique key."""
        async with self.db.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM model_cache WHERE source=$1 AND model_id=$2 AND revision=$3",
                source, model_id, revision,
            )
            return dict(row) if row else None

    async def list_all(self) -> list[dict]:
        """Return all rows, newest first."""
        async with self.db.acquire() as conn:
            return [dict(r) for r in await conn.fetch(
                "SELECT * FROM model_cache ORDER BY created_at DESC",
            )]

    async def lru_candidates(self, *, exclude_model_ids: set[str]) -> list[dict]:
        """Return cached rows ordered by ``last_used_at`` ASC (oldest first).

        Only rows with ``status='cached'`` are considered eviction candidates.
        Rows whose ``model_id`` is in *exclude_model_ids* are filtered out
        (i.e. models currently in use by a deployment).  When *exclude_model_ids*
        is empty, all cached rows are returned (``<> ALL('{}')`` is TRUE for
        every row).
        """
        async with self.db.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT * FROM model_cache
                WHERE status = 'cached'
                  AND model_id <> ALL($1::text[])
                ORDER BY last_used_at ASC
                """,
                list(exclude_model_ids),
            )
            return [dict(r) for r in rows]

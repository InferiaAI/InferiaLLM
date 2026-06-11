import json
import logging
from uuid import UUID
from cryptography.fernet import Fernet

from inferia.services.orchestration.config import settings

logger = logging.getLogger(__name__)

_encryption_key = settings.secret_encryption_key
_fernet = Fernet(_encryption_key.encode()) if _encryption_key else None


def _decrypt_value(value):
    """Decrypt the EncryptedJSON value from system_settings."""
    if not value:
        return None
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return value
    if isinstance(value, dict) and "data" in value:
        if _fernet:
            try:
                decrypted = _fernet.decrypt(value["data"].encode()).decode()
                return json.loads(decrypted)
            except Exception:
                pass
    return value


class ComputePoolRepository:
    def __init__(self, db):
        self.db = db

    async def get_provider_config(self, provider: str) -> dict:
        """
        Read provider config from system_settings (shared DB with API Gateway).
        Returns the config dict for the provider, or empty dict.
        """
        query = """
        SELECT value FROM system_settings WHERE key = 'providers_config' LIMIT 1
        """
        try:
            async with self.db.acquire() as conn:
                raw = await conn.fetchval(query)
                if not raw:
                    return {}
                data = _decrypt_value(raw)
                if not data or not isinstance(data, dict):
                    return {}
                providers = data.get("providers", data)

                # Map provider to config path
                config_paths = {
                    "gcp": ("cloud", "gcp"),
                    "aws": ("cloud", "aws"),
                    "nosana": ("depin", "nosana"),
                    "akash": ("depin", "akash"),
                }
                path = config_paths.get(provider)
                if not path:
                    return {}
                section = providers
                for key in path:
                    section = section.get(key, {})
                return section
        except Exception as e:
            logger.warning(f"Could not read provider config from DB: {e}")
            return {}

    async def credential_exists(self, provider: str, credential_name: str) -> bool:
        """
        Check if a credential exists for the given provider.

        Credentials are stored in system_settings (providers_config JSON)
        by the API Gateway, so we look them up from config first and fall
        back to the provider_credentials table for forward-compatibility.
        """
        # 1. Check config-based storage (where the API Gateway actually saves)
        config = await self.get_provider_config(provider)
        if config:
            # Nosana stores keys in api_keys[]; Akash in wallets[]
            key_lists = [
                config.get("api_keys", []),
                config.get("wallets", []),
            ]
            for entries in key_lists:
                for entry in entries:
                    if entry.get("name") == credential_name and entry.get(
                        "is_active", True
                    ):
                        return True
            # Also check legacy single-key field (nosana.api_key → name "default")
            if credential_name == "default" and config.get("api_key"):
                return True

        # 2. Fall back to provider_credentials table
        query = """
        SELECT EXISTS(
            SELECT 1 FROM provider_credentials
            WHERE provider = $1 AND name = $2 AND is_active = TRUE
        )
        """
        try:
            async with self.db.acquire() as conn:
                return await conn.fetchval(query, provider, credential_name)
        except Exception:
            return False

    async def create_pool(self, data: dict):
        query = """
        INSERT INTO compute_pools (
            pool_name,
            owner_type,
            owner_id,
            provider,
            pool_type,
            allowed_gpu_types,
            max_cost_per_hour,
            is_dedicated,
            scheduling_policy,
            provider_pool_id,
            provider_credential_name,
            cluster_id,
            region_constraint,
            is_active,
            lifecycle_state,
            gpu_count
        )
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16)
        RETURNING id
        """
        async with self.db.acquire() as conn:
            return await conn.fetchval(
                query,
                data["pool_name"],
                data["owner_type"],
                data["owner_id"],
                data["provider"],
                data.get("pool_type", "job"),
                data["allowed_gpu_types"],
                data["max_cost_per_hour"],
                data["is_dedicated"],
                data["scheduling_policy"],
                data["provider_pool_id"],
                data.get("provider_credential_name"),
                data.get("cluster_id"),
                data.get("region_constraint"),
                data.get("is_active", True),
                data.get("lifecycle_state", "running"),
                data.get("gpu_count", 1),
            )

    async def update_pool(self, pool_id: UUID, data: dict):
        query = """
        UPDATE compute_pools
        SET allowed_gpu_types = $2,
            max_cost_per_hour = $3,
            is_dedicated = $4,
            updated_at = now()
        WHERE id = $1 AND is_active = TRUE
        """
        async with self.db.acquire() as conn:
            await conn.execute(
                query,
                pool_id,
                data["allowed_gpu_types"],
                data["max_cost_per_hour"],
                data["is_dedicated"],
            )

    async def soft_delete_pool(self, pool_id: UUID):
        query = """
        UPDATE compute_pools
        SET is_active = FALSE,
            updated_at = now()
        WHERE id = $1
        """
        async with self.db.acquire() as conn:
            await conn.execute(query, pool_id)

    async def bind_provider_resource(
        self, pool_id: UUID, provider_resource_id: UUID, priority: int
    ):
        query = """
        INSERT INTO compute_pool_provider_resources
            (pool_id, provider_resource_id, priority)
        VALUES ($1, $2, $3)
        ON CONFLICT (pool_id, provider_resource_id)
        DO UPDATE SET priority = EXCLUDED.priority,
                      is_enabled = TRUE
        """
        async with self.db.acquire() as conn:
            await conn.execute(query, pool_id, provider_resource_id, priority)

    async def unbind_provider_resource(self, pool_id: UUID, provider_resource_id: UUID):
        query = """
        UPDATE compute_pool_provider_resources
        SET is_enabled = FALSE
        WHERE pool_id = $1 AND provider_resource_id = $2
        """
        async with self.db.acquire() as conn:
            await conn.execute(query, pool_id, provider_resource_id)

    async def list_pool_inventory(self, pool_id: UUID):
        query = """
        SELECT
            ci.id AS node_id,
            ci.provider,
            ci.state,
            ci.gpu_total,
            ci.gpu_allocated,
            ci.vcpu_total,
            ci.vcpu_allocated,
            ci.last_heartbeat,
            ci.created_at,
            ci.expose_url
        FROM compute_inventory ci
        WHERE ci.pool_id = $1
        """
        async with self.db.acquire() as conn:
            return await conn.fetch(query, pool_id)

    async def get(self, pool_id: UUID):
        query = """
        SELECT *
        FROM compute_pools
        WHERE id = $1 AND is_active = TRUE
        """
        async with self.db.acquire() as conn:
            return await conn.fetchrow(query, pool_id)

    async def list_pools(
        self, owner_id: str | None = None, *, limit: int = 100, offset: int = 0
    ):
        query = """
        SELECT
            id,
            pool_name,
            provider,
            pool_type,
            is_active,
            owner_type,
            owner_id,
            max_cost_per_hour,
            allowed_gpu_types,
            is_dedicated,
            scheduling_policy,
            provider_pool_id,
            provider_credential_name,
            cluster_id,
            region_constraint,
            lifecycle_state,
            gpu_count,
            updated_at,
            created_at
        FROM compute_pools
        WHERE is_active = TRUE
        """

        params = []
        param_idx = 1

        if owner_id:
            query += f" AND owner_id = ${param_idx}"
            params.append(owner_id)
            param_idx += 1

        query += f" ORDER BY created_at DESC LIMIT ${param_idx} OFFSET ${param_idx + 1}"
        params.extend([limit, offset])

        async with self.db.acquire() as conn:
            return await conn.fetch(query, *params)

    async def update_pool_cluster_id(self, pool_id: UUID, cluster_id: str):
        """Update the cluster_id for a cluster-based pool."""
        query = """
        UPDATE compute_pools
        SET cluster_id = $2,
            updated_at = now()
        WHERE id = $1
        """
        async with self.db.acquire() as conn:
            await conn.execute(query, pool_id, cluster_id)

    async def get_cluster_id(self, pool_id: UUID) -> str | None:
        """Get the cluster_id for a pool."""
        query = """
        SELECT cluster_id
        FROM compute_pools
        WHERE id = $1
        """
        async with self.db.acquire() as conn:
            return await conn.fetchval(query, pool_id)

    async def set_pool_active(self, pool_id: UUID, is_active: bool):
        """Set the active status of a pool."""
        query = """
        UPDATE compute_pools
        SET is_active = $2,
            updated_at = now()
        WHERE id = $1
        """
        async with self.db.acquire() as conn:
            await conn.execute(query, pool_id, is_active)

    async def set_pool_lifecycle_state(self, pool_id: UUID, lifecycle_state: str):
        query = """
        UPDATE compute_pools
        SET lifecycle_state = $2,
            updated_at = now()
        WHERE id = $1 AND is_active = TRUE
        """
        async with self.db.acquire() as conn:
            await conn.execute(query, pool_id, lifecycle_state)

    async def count_nodes(self, pool_id: UUID) -> int:
        """Count nodes that occupy a slot in the pool — ready or
        provisioning, not flagged as terminating. Used by PoolPlacer to
        enforce compute_pools.max_nodes (T5)."""
        async with self.db.acquire() as conn:
            row = await conn.fetchval(
                """
                SELECT COUNT(*) FROM compute_inventory
                 WHERE pool_id = $1
                   AND state IN ('ready', 'provisioning')
                   AND (metadata->>'terminating') IS DISTINCT FROM 'true'
                """,
                pool_id,
            )
        return int(row or 0)

    async def count_live_inventory(self, pool_id: UUID) -> int:
        """Count EVERY ``compute_inventory`` row still attached to the pool,
        regardless of state.

        Unlike :meth:`count_nodes` (which is a scheduler capacity gate and so
        filters to ready/provisioning, excluding terminating rows), this is the
        teardown progress signal the pool finalizer keys off: a pool is only
        finalized (hard-deleted) once its LAST node's inventory row has been
        purged. ``purge_node`` HARD-deletes the row, so a fully-torn-down pool
        returns 0 here — at which point the pool row + its pool-scoped residue
        can be safely removed.
        """
        async with self.db.acquire() as conn:
            row = await conn.fetchval(
                "SELECT COUNT(*) FROM compute_inventory WHERE pool_id = $1",
                pool_id,
            )
        return int(row or 0)

    async def get_lifecycle_state(self, pool_id: UUID) -> str | None:
        """Return the pool's ``lifecycle_state`` (or None if the row is gone).

        Reads WITHOUT the ``is_active = TRUE`` filter the public ``get`` uses,
        because a pool mid-teardown has already been soft-deleted
        (``is_active = FALSE``) by the delete request — the finalizer still
        needs to see its ``lifecycle_state = 'terminating'`` to decide whether
        to hard-delete it.
        """
        async with self.db.acquire() as conn:
            return await conn.fetchval(
                "SELECT lifecycle_state FROM compute_pools WHERE id = $1",
                pool_id,
            )

    async def finalize_pool_delete(self, pool_id: UUID, *, tx=None) -> bool:
        """HARD-delete a fully-torn-down pool and its pool-scoped DB residue.

        Phase 2 of the two-phase pool teardown. The delete request only
        SOFT-deletes the pool (``is_active = FALSE`` /
        ``lifecycle_state = 'terminating'``) and fires per-node teardown via
        ``force_cancel_pool``; the EC2 destroys are async (the reconciler's
        CancelHandler runs ``pulumi destroy`` per node). This finalizer is the
        SECOND phase — invoked once the LAST node has been purged (no
        ``compute_inventory`` rows remain) — and removes everything the
        soft-delete left behind so a deleted pool eventually leaves ZERO DB
        residue:

          1. ``node_provisioning_events`` — ``pool_id`` has NO FK (plain UUID
             column), so the ``compute_pools`` cascade never reaches it; the
             per-node purge already removed by-node rows, this catches any
             pool-only / orphan rows.
          2. ``worker_bootstrap_tokens`` — although ``pool_id`` IS an
             ``ON DELETE CASCADE`` FK, we delete it explicitly so UNconsumed
             pool tokens are gone deterministically (consumed-by-node tokens
             were already purged per node), and so the behaviour is identical
             whether or not the cascade fires first.
          3. ``compute_pools`` — the HARD delete. Removing the row finally lets
             the ``ON DELETE CASCADE`` FKs that pointed AT it fire
             (``autoscaler_state``, plus any straggler ``compute_inventory`` /
             ``model_deployments`` / ``worker_bootstrap_tokens.pool_id`` rows),
             and frees the UNIQUE(pool_name, owner_type, owner_id) so a
             same-named pool can be re-created.

        Runs as ONE transaction (the caller's ``tx`` when supplied, else a
        freshly acquired connection + transaction). The boto3
        ``sweep_pool_instances`` orphan/duplicate-EC2 backstop is deliberately
        NOT part of this method — it must run OUTSIDE the DB transaction
        (boto3 is sync + best-effort); the reconciler's ``_teardown_node``
        invokes it after this returns.

        Returns True if the ``compute_pools`` row was actually deleted, False
        if it was already gone (idempotent — safe to call twice).
        """

        async def _run(conn) -> bool:
            await conn.execute(
                "DELETE FROM node_provisioning_events WHERE pool_id = $1",
                pool_id,
            )
            await conn.execute(
                "DELETE FROM worker_bootstrap_tokens WHERE pool_id = $1",
                pool_id,
            )
            # Detach any SURVIVING deployment whose ``target_pool_id`` still
            # points at this pool. ``model_deployments.target_pool_id`` is an
            # ON DELETE NO ACTION FK (the 20260530 migration added it with no
            # ON DELETE clause), so a row that diverged — ``pool_id`` re-placed
            # onto another live pool while ``target_pool_id`` still references
            # THIS one — would make the ``DELETE FROM compute_pools`` below
            # raise ForeignKeyViolation, rolling the whole finalize back and
            # leaving the pool stuck 'terminating' forever. Rows with
            # ``pool_id == this`` cascade-delete anyway (that FK IS ON DELETE
            # CASCADE), so this NULL-out only affects the divergent stragglers.
            await conn.execute(
                "UPDATE model_deployments SET target_pool_id = NULL "
                "WHERE target_pool_id = $1",
                pool_id,
            )
            res = await conn.execute(
                "DELETE FROM compute_pools WHERE id = $1",
                pool_id,
            )
            # asyncpg DELETE status is 'DELETE <rowcount>'.
            return res.rsplit(" ", 1)[-1] == "1"

        if tx is not None:
            return await _run(tx)
        async with self.db.acquire() as conn:
            async with conn.transaction():
                return await _run(conn)


# ---------------------------------------------------------------------------
# Worker-agent extensions (inferia-worker integration).
# ---------------------------------------------------------------------------


async def _get_or_generate_inference_token_impl(self, *, pool_id):
    """Return the pool's inference_token; generate one on first call.

    Uses ``UPDATE ... SET inference_token = COALESCE(inference_token, $2)
    RETURNING inference_token`` so concurrent first-callers converge on a
    single persisted value (whichever transaction wins).
    """
    import secrets
    async with self.db.acquire() as conn:
        existing = await conn.fetchval(
            "SELECT inference_token FROM compute_pools WHERE id = $1",
            pool_id,
        )
        if existing:
            return existing

        # Generate and persist, COALESCE-protecting against a race.
        proposed = secrets.token_urlsafe(32)
        return await conn.fetchval(
            """
            UPDATE compute_pools
            SET inference_token = COALESCE(inference_token, $2),
                updated_at = now()
            WHERE id = $1
            RETURNING inference_token
            """,
            pool_id, proposed,
        )


async def _rotate_inference_token_impl(self, *, pool_id):
    """Force-generate a new inference_token for the pool and return it.

    Workers using the old value will fail inference auth after this call —
    operators are expected to redeploy them with a fresh env_snippet.
    """
    import secrets
    new_value = secrets.token_urlsafe(32)
    async with self.db.acquire() as conn:
        return await conn.fetchval(
            """
            UPDATE compute_pools
            SET inference_token = $2,
                updated_at = now()
            WHERE id = $1
            RETURNING inference_token
            """,
            pool_id, new_value,
        )


ComputePoolRepository.get_or_generate_inference_token = (
    _get_or_generate_inference_token_impl
)
ComputePoolRepository.rotate_inference_token = _rotate_inference_token_impl


# ---------------------------------------------------------------------------
# Default-pool-per-org (node-centric refactor).
# ---------------------------------------------------------------------------


async def _ensure_default_pool_impl(self, *, org_id):
    """Return the org's __default__ pool uuid, creating it if needed.

    The migration backfills one per existing org; this method handles new
    orgs created after the migration. Concurrent first-callers converge
    because the SELECT-then-INSERT pattern below uses ON CONFLICT DO
    NOTHING on (owner_id, pool_name) — though, since the table has no such
    constraint today, we rely on the existence check + the rarity of the
    race. Worst case we get two rows for an org's first concurrent
    addition; the next get serves whichever was inserted last. Acceptable
    for MVP; the unique partial index is a follow-up.
    """
    async with self.db.acquire() as conn:
        existing = await conn.fetchval(
            """
            SELECT id FROM compute_pools
            WHERE owner_type = 'organization'
              AND owner_id = $1
              AND pool_name = '__default__'
            LIMIT 1
            """,
            org_id,
        )
        if existing:
            return existing
        new_id = await conn.fetchval(
            """
            INSERT INTO compute_pools (
                pool_name, owner_type, owner_id, provider, pool_type,
                allowed_gpu_types, max_cost_per_hour, scheduling_policy,
                provider_pool_id, is_active
            )
            VALUES (
                '__default__', 'organization', $1, 'on_prem', 'job',
                ARRAY['any']::text[], 0, '{}'::jsonb,
                'default:' || $1, true
            )
            RETURNING id
            """,
            org_id,
        )
        return new_id


ComputePoolRepository.ensure_default_pool = _ensure_default_pool_impl

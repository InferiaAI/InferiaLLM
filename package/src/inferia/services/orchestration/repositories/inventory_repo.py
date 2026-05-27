from uuid import UUID
import json
from inferia.services.orchestration.constants import NodeState


class InventoryRepository:
    def __init__(self, db):
        self.db = db

    async def inven_register_node(
        self,
        *,
        pool_id,
        provider,
        provider_instance_id,
        hostname,
        gpu_total,
        vcpu_total,
        ram_gb_total,
        state,
    ):
        return await self.db.fetchval(
            """
            INSERT INTO compute_inventory (
                pool_id,
                provider,
                provider_instance_id,
                hostname,
                gpu_total,
                vcpu_total,
                ram_gb_total,
                state
            )
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
            RETURNING id
            """,
            pool_id,
            provider,
            provider_instance_id,
            hostname,
            gpu_total,
            vcpu_total,
            ram_gb_total,
            state,
        )

    async def heartbeat(self, data: dict):
        query = """
        UPDATE compute_inventory
        SET
            gpu_allocated = $3,
            vcpu_allocated = $4,
            ram_gb_allocated = $5,
            health_score = $6,
            state = $7,
            expose_url = $8,
            last_heartbeat = now(),
            updated_at = now()
        WHERE provider = $1
          AND provider_instance_id = $2
        RETURNING id, expose_url
        """

        # Map incoming states to valid DB enum values
        # Valid enum: ordered, provisioning, ready, busy, unhealthy, terminated, offline
        incoming_state = NodeState.from_incoming(data["state"])

        async with self.db.acquire() as conn:
            # Handle redeployment: if old_provider_instance_id is provided, swap the
            # old node record to the new provider_instance_id so the deployment's
            # node_ids reference stays valid and heartbeats continue on the same row.
            old_instance_id = data.get("old_provider_instance_id")
            if old_instance_id:
                await conn.execute(
                    """
                    UPDATE compute_inventory
                    SET provider_instance_id = $1,
                        updated_at = now()
                    WHERE provider = $2
                      AND provider_instance_id = $3
                    """,
                    data["provider_instance_id"],
                    data["provider"],
                    old_instance_id,
                )

            row = await conn.fetchrow(
                query,
                data["provider"],
                data["provider_instance_id"],
                data["gpu_allocated"],
                data["vcpu_allocated"],
                data["ram_gb_allocated"],
                data["health_score"],
                incoming_state,
                data.get("expose_url"),
            )

            if row:
                res = dict(row)
                node_id = res["id"]
                expose_url = res["expose_url"]

                # Sync logic: If we have an endpoint URL, ensure associated deployments have it
                if expose_url:
                    await conn.execute(
                        """
                        UPDATE model_deployments
                        SET endpoint = $1, updated_at = now()
                        WHERE node_ids @> ARRAY[$2]::uuid[]
                          AND (endpoint IS NULL OR endpoint != $1)
                        """,
                        expose_url,
                        node_id,
                    )
                return res
            return None

    async def mark_unhealthy(self, timeout_seconds: int):
        query = """
        UPDATE compute_inventory
        SET state = 'unhealthy',
            updated_at = now()
        WHERE state IN ('ready', 'busy')
          AND last_heartbeat IS NOT NULL
          AND now() - last_heartbeat > make_interval(secs => $1)
          AND COALESCE(node_class, '') != 'cluster'
        RETURNING id
        """
        async with self.db.acquire() as conn:
            rows = await conn.fetch(query, timeout_seconds)
            return [row["id"] for row in rows]

    async def ensure_node_exists(self, data: dict):
        query = """
        INSERT INTO compute_inventory (
            pool_id,
            provider,
            provider_instance_id,
            hostname,
            gpu_total,
            gpu_allocated,
            vcpu_total,
            vcpu_allocated,
            ram_gb_total,
            ram_gb_allocated,
            state,
            health_score,
            last_heartbeat
        )
        VALUES (
            $1,$2,$3,$4,$5,0,$6,0,$7,0,'provisioning',100,now()
        )
        ON CONFLICT (provider, provider_instance_id)
        DO NOTHING
        """
        async with self.db.acquire() as conn:
            await conn.execute(
                query,
                data["pool_id"],
                data["provider"],
                data["provider_instance_id"],
                data["hostname"],
                data["gpu_total"],
                data["vcpu_total"],
                data["ram_gb_total"],
            )

    async def mark_ready_after_boot(self):
        query = """
        UPDATE compute_inventory
        SET
            state = 'unhealthy',
            updated_at = now()
        WHERE
            state = 'provisioning'
            AND created_at <= now() - INTERVAL '60 seconds'
        RETURNING id
        """

        async with self.db.acquire() as conn:
            rows = await conn.fetch(query)
            return len(rows)

    async def register_node(
        self,
        *,
        pool_id: UUID,
        provider: str,
        provider_instance_id: str,
        provider_resource_id: UUID | None,
        hostname: str,
        gpu_total: int,
        vcpu_total: int,
        ram_gb_total: int,
        state: str,
        node_class: str,
        metadata: dict,
        expose_url: str | None = None,
    ):
        async with self.db.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO compute_inventory (
                    pool_id,
                    provider,
                    provider_instance_id,
                    provider_resource_id,
                    hostname,
                    gpu_total,
                    vcpu_total,
                    ram_gb_total,
                    state,
                    node_class,
                    metadata,
                    expose_url,
                    last_heartbeat
                )
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,now())
                ON CONFLICT (provider, provider_instance_id)
                DO UPDATE SET
                    expose_url = COALESCE(compute_inventory.expose_url, EXCLUDED.expose_url),
                    state = EXCLUDED.state,
                    last_heartbeat = now(),
                    updated_at = now()
                RETURNING id
                """,
                pool_id,
                provider,
                provider_instance_id,
                provider_resource_id,
                hostname,
                gpu_total,
                vcpu_total,
                ram_gb_total,
                state,
                node_class,
                json.dumps(metadata),
                expose_url,
            )
            return row["id"] if row else None

    async def mark_ready(self, *, node_id, last_heartbeat):
        await self.db.execute(
            """
            UPDATE compute_inventory
            SET
              state = 'ready',
              last_heartbeat = $2,
              updated_at = now()
            WHERE id = $1
              AND state = 'provisioning'
            """,
            node_id,
            last_heartbeat,
        )

    async def update_heartbeat(self, *, node_id, last_heartbeat):
        await self.db.execute(
            """
            UPDATE compute_inventory
            SET
              last_heartbeat = $2,
              updated_at = now()
            WHERE id = $1
            """,
            node_id,
            last_heartbeat,
        )

    async def update_usage(
        self,
        *,
        node_id,
        gpu_allocated,
        vcpu_allocated,
        ram_gb_allocated,
        health_score,
    ):
        await self.db.execute(
            """
            UPDATE compute_inventory
            SET
              gpu_allocated=$2,
              vcpu_allocated=$3,
              ram_gb_allocated=$4,
              health_score=$5,
              updated_at=now()
            WHERE id=$1
            """,
            node_id,
            gpu_allocated,
            vcpu_allocated,
            ram_gb_allocated,
            health_score,
        )

    async def get_resource_requirement(self, pool_id: UUID):
        query = """
        SELECT
            gpu_total,
            vcpu_total,
            ram_gb_total
        FROM compute_inventory
        WHERE pool_id = $1
        """
        async with self.db.acquire() as conn:
            row = await conn.fetchrow(query, pool_id)
            if row:
                return {
                    "gpu_total": row["gpu_total"],
                    "vcpu_total": row["vcpu_total"],
                    "ram_gb_total": row["ram_gb_total"],
                }
            return None

    async def get_pool_by_id(self, pool_id: UUID):
        """Return all inventory nodes belonging to a pool."""
        query = """
        SELECT *
        FROM compute_inventory
        WHERE pool_id = $1
        """
        async with self.db.acquire() as conn:
            rows = await conn.fetch(query, pool_id)
            return [dict(r) for r in rows]

    async def get_node_by_id(self, node_id: UUID):
        query = """
        SELECT *
        FROM compute_inventory
        WHERE id = $1
        """
        async with self.db.acquire() as conn:
            row = await conn.fetchrow(query, node_id)
            if not row:
                return None
            data = dict(row)
            if data.get("metadata") and isinstance(data["metadata"], str):
                try:
                    data["metadata"] = json.loads(data["metadata"])
                except Exception:
                    pass
            return data

    async def mark_terminated(self, node_id: UUID):
        query = """
        UPDATE compute_inventory
        SET
            state = 'terminated',
            updated_at = now()
        WHERE id = $1
        """
        async with self.db.acquire() as conn:
            await conn.execute(query, node_id)

    async def recycle_node(self, node_id: UUID):
        query = """
        UPDATE compute_inventory
        SET
            state = 'ready',
            gpu_allocated = 0,
            vcpu_allocated = 0,
            ram_gb_allocated = 0,
            updated_at = now()
        WHERE id = $1
        """
        async with self.db.acquire() as conn:
            await conn.execute(query, node_id)

    async def get_deployments_for_node(self, node_id: UUID) -> list[UUID]:
        """Find all model deployments assigned to this node."""
        query = """
        SELECT deployment_id FROM model_deployments
        WHERE node_ids @> ARRAY[$1]::uuid[]
        """
        async with self.db.acquire() as conn:
            rows = await conn.fetch(query, node_id)
            return [row["deployment_id"] for row in rows]

    async def list_nodes_by_provider(
        self, provider: str, *, limit: int = 200, offset: int = 0
    ) -> list[dict]:
        """List nodes for a specific provider with pagination."""
        query = """
        SELECT
            id,
            pool_id,
            provider,
            provider_instance_id,
            hostname,
            gpu_total,
            gpu_allocated,
            vcpu_total,
            vcpu_allocated,
            ram_gb_total,
            ram_gb_allocated,
            state,
            health_score,
            expose_url,
            last_heartbeat,
            created_at,
            updated_at
        FROM compute_inventory
        WHERE provider = $1
        ORDER BY created_at DESC
        LIMIT $2 OFFSET $3
        """
        async with self.db.acquire() as conn:
            rows = await conn.fetch(query, provider, limit, offset)
            return [dict(row) for row in rows]


# ---------------------------------------------------------------------------
# Worker-agent extensions (inferia-worker integration).
# ---------------------------------------------------------------------------


class DuplicateNodeError(Exception):
    """Raised by upsert_worker when (pool_id, node_name) is held by a row
    whose agent_kind is not 'worker'."""


async def _upsert_worker_impl(
    self, *, pool_id, node_name, advertise_url, allocatable, labels=None,
):
    """Upsert a (pool_id, node_name) row with agent_kind='worker'.

    Returns the row as a dict. Raises DuplicateNodeError if (pool_id,
    node_name) is held by a non-worker-kind row — those cannot be
    re-purposed in place.

    `labels` is a flat string→string dict (e.g. runtime_env / instance_id /
    region / availability_zone supplied by the worker's cloudenv probe).
    Stored in the `labels` jsonb column for filterable queries.
    """
    labels_json = __import__("json").dumps(labels or {})
    # 1. Probe — does a row already exist for this (pool_id, node_name)?
    async with self.db.acquire() as conn:
        existing = await conn.fetchrow(
            """
            SELECT id, pool_id, node_name, agent_kind, state, advertise_url
            FROM compute_inventory
            WHERE pool_id = $1 AND node_name = $2
            """,
            pool_id, node_name,
        )

        if existing and existing.get("agent_kind") not in (None, "worker"):
            raise DuplicateNodeError(
                f"{pool_id}/{node_name} is held by a non-worker node"
            )

        # 2. Upsert. Use INSERT ON CONFLICT (pool_id, node_name) WHERE
        #    agent_kind='worker' — the migration created a matching partial
        #    unique index for exactly this purpose.
        row = await conn.fetchrow(
            """
            INSERT INTO compute_inventory (
                pool_id, node_name, agent_kind, state,
                advertise_url, gpu_total, vcpu_total, ram_gb_total,
                provider, provider_instance_id, metadata, labels
            )
            VALUES ($1, $2, 'worker', 'provisioning', $3,
                    COALESCE(($4::jsonb ->> 'gpu')::int, 0),
                    COALESCE(($4::jsonb ->> 'cpu')::int, 0),
                    COALESCE(($4::jsonb ->> 'memory_gb')::int, 0),
                    'on_prem', $5, $4, $6::jsonb)
            ON CONFLICT (pool_id, node_name)
                WHERE agent_kind = 'worker' AND node_name IS NOT NULL
            DO UPDATE SET
                advertise_url = EXCLUDED.advertise_url,
                metadata = EXCLUDED.metadata,
                labels = compute_inventory.labels || EXCLUDED.labels,
                -- Resource totals are refreshed only when the incoming
                -- payload actually reports a non-zero value. The
                -- /api/v1/nodes/add/worker route pre-creates a placeholder
                -- row with allocatable={} (so the dashboard shows the
                -- pending node immediately); the worker's later register
                -- call carries the real capacity probed by telemetry on
                -- the host. Without these COALESCE-on-non-zero updates,
                -- the placeholder's zeros would shadow the worker's real
                -- numbers and the scheduler would refuse to place any
                -- GPU/CPU/RAM workload onto the node.
                gpu_total = CASE WHEN EXCLUDED.gpu_total > 0
                                 THEN EXCLUDED.gpu_total
                                 ELSE compute_inventory.gpu_total END,
                vcpu_total = CASE WHEN EXCLUDED.vcpu_total > 0
                                  THEN EXCLUDED.vcpu_total
                                  ELSE compute_inventory.vcpu_total END,
                ram_gb_total = CASE WHEN EXCLUDED.ram_gb_total > 0
                                    THEN EXCLUDED.ram_gb_total
                                    ELSE compute_inventory.ram_gb_total END,
                updated_at = now()
            RETURNING id, pool_id, node_name, agent_kind, state, advertise_url
            """,
            pool_id, node_name, advertise_url,
            __import__("json").dumps(allocatable),
            f"worker-{pool_id}-{node_name}",
            labels_json,
        )
        return dict(row) if row else {}


async def _list_workers_impl(self, *, pool_id):
    """All agent_kind='worker' rows in the pool, ordered by created_at."""
    async with self.db.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, pool_id, node_name, agent_kind, state,
                   advertise_url, gpu_total, gpu_allocated,
                   vcpu_total, vcpu_allocated, ram_gb_total,
                   ram_gb_allocated, last_heartbeat, metadata,
                   created_at, updated_at
            FROM compute_inventory
            WHERE pool_id = $1 AND agent_kind = 'worker'
            ORDER BY created_at ASC
            """,
            pool_id,
        )
        return [dict(r) for r in rows]


async def _update_heartbeat_with_telemetry_impl(self, *, node_id, used, loaded_models):
    """Persist heartbeat fields specific to the worker protocol."""
    payload = __import__("json").dumps({"used": used, "loaded_models": loaded_models})
    async with self.db.acquire() as conn:
        await conn.execute(
            """
            UPDATE compute_inventory
            SET
              last_heartbeat = now(),
              metadata = COALESCE(metadata, '{}'::jsonb) || $2::jsonb,
              updated_at = now()
            WHERE id = $1
            """,
            node_id, payload,
        )


async def _mark_ready_worker_impl(self, *, node_id):
    """Transition a worker row from provisioning → ready. No-op otherwise."""
    async with self.db.acquire() as conn:
        await conn.execute(
            """
            UPDATE compute_inventory
            SET state = 'ready', updated_at = now()
            WHERE id = $1
              AND state = 'provisioning'
              AND agent_kind = 'worker'
            """,
            node_id,
        )


async def _mark_terminated_worker_impl(self, *, node_id):
    """Transition a worker row to terminated. Idempotent."""
    async with self.db.acquire() as conn:
        await conn.execute(
            """
            UPDATE compute_inventory
            SET state = 'terminated', updated_at = now()
            WHERE id = $1 AND agent_kind = 'worker'
            """,
            node_id,
        )


# Attach the worker-agent methods to the existing repository class.
InventoryRepository.upsert_worker = _upsert_worker_impl
InventoryRepository.list_workers = _list_workers_impl
InventoryRepository.update_heartbeat_with_telemetry = (
    _update_heartbeat_with_telemetry_impl
)
InventoryRepository.mark_ready_worker = _mark_ready_worker_impl
InventoryRepository.mark_terminated_worker = _mark_terminated_worker_impl


# ---------------------------------------------------------------------------
# Node-centric extensions (see docs/specs/2026-05-14-node-centric-refactor.md).
# ---------------------------------------------------------------------------


class NodeNotFoundError(Exception):
    """Raised by set_labels / get_node when no inventory row matches the id."""


class NodeTerminatedError(Exception):
    """Raised when an operation is attempted on a terminated node."""


class LabelConflictError(Exception):
    """Raised by set_labels when the same key appears in both add and remove."""


_MAX_LABELS = 32
_MAX_KEY_LEN = 253
_MAX_VAL_LEN = 253


def _validate_labels(add: dict, remove: list[str]) -> None:
    overlap = set(add.keys()) & set(remove)
    if overlap:
        raise LabelConflictError(
            f"labels appear in both add and remove: {sorted(overlap)}"
        )
    if len(add) + len(remove) > _MAX_LABELS:
        raise ValueError(f"labels payload exceeds {_MAX_LABELS} entries")
    for k, v in add.items():
        if not isinstance(k, str) or not k or len(k) > _MAX_KEY_LEN:
            raise ValueError(f"label key length must be 1..{_MAX_KEY_LEN}: {k!r}")
        if not isinstance(v, str) or len(v) > _MAX_VAL_LEN:
            raise ValueError(f"label value length must be 0..{_MAX_VAL_LEN}: {v!r}")
        if any(ord(c) < 0x20 for c in k) or any(ord(c) < 0x20 for c in v):
            raise ValueError("label keys/values must not contain control characters")
    for k in remove:
        if not isinstance(k, str) or not k:
            raise ValueError(f"label remove entries must be non-empty strings: {k!r}")


async def _list_nodes_impl(self, *, org_id, selector=None):
    """All non-terminated compute_inventory rows attached to a pool owned by
    org_id, optionally filtered by a label selector (AND across keys)."""
    # Filter on owner_id only so we catch both canonical
    # owner_type='organization' rows and the legacy createpool path which
    # writes owner_type='user' with owner_id set to the org's UUID anyway.
    sql = (
        "SELECT i.id, i.pool_id, i.node_name, i.agent_kind, i.state,"
        "       i.advertise_url, i.expose_url, i.gpu_total, i.gpu_allocated,"
        "       i.vcpu_total, i.vcpu_allocated, i.ram_gb_total,"
        "       i.ram_gb_allocated, i.last_heartbeat, i.labels,"
        "       i.metadata, i.provider, i.provider_instance_id,"
        "       i.hostname, i.created_at, i.updated_at "
        "FROM compute_inventory i "
        "JOIN compute_pools p ON p.id = i.pool_id "
        "WHERE p.owner_id = $1 "
        "  AND i.state IS DISTINCT FROM 'terminated' "
    )
    args: list = [org_id]
    if selector:
        sql += " AND i.labels @> $2::jsonb "
        args.append(__import__("json").dumps(selector))
    sql += " ORDER BY i.created_at ASC"
    async with self.db.acquire() as conn:
        rows = await conn.fetch(sql, *args)
        return [dict(r) for r in rows]


async def _get_node_impl(self, *, node_id):
    async with self.db.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT id, pool_id, node_name, agent_kind, state,
                   advertise_url, expose_url, gpu_total, gpu_allocated,
                   vcpu_total, vcpu_allocated, ram_gb_total, ram_gb_allocated,
                   last_heartbeat, labels, metadata,
                   provider, provider_instance_id, created_at, updated_at
            FROM compute_inventory
            WHERE id = $1
            """,
            node_id,
        )
        return dict(row) if row else None


async def _set_labels_impl(self, *, node_id, add, remove):
    """Apply an add/remove patch to compute_inventory.labels."""
    _validate_labels(add, remove)
    async with self.db.acquire() as conn:
        existing = await conn.fetchrow(
            "SELECT id, labels, state FROM compute_inventory WHERE id = $1",
            node_id,
        )
        if not existing:
            raise NodeNotFoundError(f"node {node_id} not found")
        if existing.get("state") == "terminated":
            raise NodeTerminatedError(f"node {node_id} is terminated")
        # asyncpg returns jsonb as a string by default (no codec registered).
        # Decode before dict() so we don't trip "dictionary update sequence" on
        # the literal '{}' the DB column defaults to.
        current = existing.get("labels") or {}
        if isinstance(current, str):
            try:
                current = __import__("json").loads(current)
            except Exception:
                current = {}
        merged = dict(current)
        merged.update(add)
        for k in remove:
            merged.pop(k, None)
        row = await conn.fetchrow(
            """
            UPDATE compute_inventory
            SET labels = $2::jsonb,
                updated_at = now()
            WHERE id = $1
            RETURNING id, labels, state, pool_id, node_name, agent_kind,
                      advertise_url, gpu_total, gpu_allocated,
                      vcpu_total, vcpu_allocated, ram_gb_total, ram_gb_allocated,
                      last_heartbeat, provider
            """,
            node_id, __import__("json").dumps(merged),
        )
        return dict(row)


async def _soft_delete_node_impl(self, *, node_id):
    """Transition any inventory row to state='terminated'. Idempotent."""
    async with self.db.acquire() as conn:
        await conn.execute(
            """
            UPDATE compute_inventory
            SET state = 'terminated', updated_at = now()
            WHERE id = $1
            """,
            node_id,
        )


async def _mark_terminating_node_impl(self, *, node_id):
    """Mark a node as terminating ahead of an async destroy task.

    The node_state Postgres enum does NOT yet include 'terminating'
    (see global_schema.sql); rather than push a destructive migration
    we record the transition by stamping ``metadata.terminating=true``
    so the dashboard can render the in-flight state. The actual SQL
    enum still reads 'ready' or 'provisioning' until the destroy
    completes and ``soft_delete_node`` / the deprovision helper sets
    'terminated'. Idempotent.
    """
    async with self.db.acquire() as conn:
        await conn.execute(
            """
            UPDATE compute_inventory
            SET metadata = COALESCE(metadata, '{}'::jsonb)
                           || jsonb_build_object('terminating', true,
                                                 'terminating_at', now()::text),
                updated_at = now()
            WHERE id = $1
            """,
            node_id,
        )


InventoryRepository.list_nodes = _list_nodes_impl
InventoryRepository.get_node = _get_node_impl
InventoryRepository.set_labels = _set_labels_impl
InventoryRepository.soft_delete_node = _soft_delete_node_impl
InventoryRepository.mark_terminating_node = _mark_terminating_node_impl

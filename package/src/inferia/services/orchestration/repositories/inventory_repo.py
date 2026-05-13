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


async def _upsert_worker_impl(self, *, pool_id, node_name, advertise_url, allocatable):
    """Upsert a (pool_id, node_name) row with agent_kind='worker'.

    Returns the row as a dict. Raises DuplicateNodeError if (pool_id,
    node_name) is held by a non-worker-kind row — those cannot be
    re-purposed in place.
    """
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
                provider, provider_instance_id, metadata
            )
            VALUES ($1, $2, 'worker', 'provisioning', $3,
                    COALESCE(($4::jsonb ->> 'gpu')::int, 0),
                    COALESCE(($4::jsonb ->> 'cpu')::int, 0),
                    COALESCE(($4::jsonb ->> 'memory_gb')::int, 0),
                    'on_prem', $5, $4)
            ON CONFLICT (pool_id, node_name)
                WHERE agent_kind = 'worker' AND node_name IS NOT NULL
            DO UPDATE SET
                advertise_url = EXCLUDED.advertise_url,
                metadata = EXCLUDED.metadata,
                updated_at = now()
            RETURNING id, pool_id, node_name, agent_kind, state, advertise_url
            """,
            pool_id, node_name, advertise_url,
            __import__("json").dumps(allocatable),
            f"worker-{pool_id}-{node_name}",
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

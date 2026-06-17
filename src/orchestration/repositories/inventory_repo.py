import uuid
from uuid import UUID, uuid4
import json
import logging
from dataclasses import dataclass
from orchestration.constants import NodeState

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ReleaseResult:
    new_allocated: int
    should_destroy: bool


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

    async def create_provisioning_placeholder(
        self,
        *,
        pool_id,
        provider: str,
        instance_class: str,
        instance_type: str,
        node_name: str | None = None,
    ) -> UUID:
        """Insert a 'provisioning' placeholder row into compute_inventory.

        Used by the thin-enqueue POST /v1/nodes/add/aws path: the HTTP
        handler must return a node_id immediately so the dashboard can
        link to /nodes/{id} while the reconciler does the actual Pulumi
        work asynchronously.

        The provider_instance_id is set to a unique 'placeholder:<uuid>'
        sentinel because the real EC2 instance-id is not known until the
        Pulumi up phase completes — and the UNIQUE(provider,
        provider_instance_id) constraint on compute_inventory means we
        cannot leave it NULL or share a value across rows. The
        PulumiUpHandler swaps this value for the real i-XXXXXXXXXXX when
        the stack reaches 'ec2_running'.

        agent_kind is set to 'worker' because AWS-provisioned VMs run the
        inferia-worker agent (the worker bootstrap phase installs it).

        Returns the generated node_id (UUID).
        """
        placeholder_instance_id = f"placeholder:{uuid.uuid4()}"
        hostname = node_name or placeholder_instance_id
        async with self.db.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO compute_inventory (
                    pool_id,
                    provider,
                    provider_instance_id,
                    hostname,
                    state,
                    agent_kind,
                    instance_class,
                    instance_type,
                    node_name
                )
                VALUES ($1, $2, $3, $4, 'provisioning', 'worker', $5, $6, $7)
                RETURNING id
                """,
                pool_id,
                provider,
                placeholder_instance_id,
                hostname,
                instance_class,
                instance_type,
                node_name,
            )
            return row["id"]

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

    async def mark_destroy_failed(self, node_id, reason: str, *, tx=None) -> None:
        """Record a pulumi-destroy failure on the inventory row WITHOUT
        flipping ``state`` to terminated.

        When the reconciler's CancelHandler runs ``pulumi destroy`` and it
        raises a *real* failure (not the idempotent "missing stack" case),
        the node must NOT silently show terminated while its EC2 keeps
        billing. We stamp two metadata flags — ``destroy_failed=true`` and
        ``destroy_error=<reason>`` — and leave the SQL ``state`` column
        untouched so the job can stay retryable and the dashboard can render
        a "teardown failed" banner. Mirrors the metadata shape written by
        ``aws_deprovision._mark_destroy_failed`` so existing greps/tests for
        ``destroy_failed`` keep working.

        Idempotent. Pass ``tx`` to run inside a caller's transaction.
        """
        q = """
            UPDATE compute_inventory
            SET metadata = COALESCE(metadata, '{}'::jsonb)
                           || jsonb_build_object(
                                  'destroy_failed', true,
                                  'destroy_error', $2::text
                              ),
                updated_at = now()
            WHERE id = $1
            """
        if tx is not None:
            await tx.execute(q, node_id, reason)
        else:
            async with self.db.acquire() as conn:
                await conn.execute(q, node_id, reason)

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

    async def allocate_gpu(
        self,
        node_id: UUID,
        count: int,
        *,
        tx=None,
    ) -> bool:
        """Atomically increment gpu_allocated by `count` iff capacity allows.

        Returns True on success, False on capacity exhaustion. Concurrent
        allocate_gpu calls on the same node racing for the last slot will
        see exactly one winner.

        Allowed states: 'ready' (warm bind) and 'provisioning' (co-wait
        on a placeholder that hasn't booted yet). Nodes flagged
        metadata.terminating=true are excluded.

        Pass `tx` to run inside a caller's transaction (so the row lock
        lives until the caller commits).
        """
        q = """
            UPDATE compute_inventory
               SET gpu_allocated = gpu_allocated + $2
             WHERE id = $1
               AND state IN ('ready', 'provisioning')
               AND (metadata->>'terminating') IS DISTINCT FROM 'true'
               AND gpu_allocated + $2 <= gpu_total
          RETURNING 1
            """
        if tx is not None:
            row = await tx.fetchrow(q, node_id, count)
        else:
            async with self.db.acquire() as conn:
                row = await conn.fetchrow(q, node_id, count)
        return row is not None

    async def release_gpu(
        self,
        node_id: UUID,
        count: int,
        *,
        tx=None,
    ) -> ReleaseResult:
        """Decrement gpu_allocated and signal whether the node should be
        destroyed (last reference released AND no PENDING_NODE/DEPLOYING/
        RUNNING deploys still target it).

        UPDATE + EXISTS run in one CTE so both reads come from a single
        snapshot — eliminates the race where a deploy is inserted between
        the decrement and the destroy check.

        Pass `tx` to run inside a caller's transaction.
        """
        q = """
            WITH dec AS (
              UPDATE compute_inventory
                 SET gpu_allocated = gpu_allocated - $2
               WHERE id = $1
                 AND gpu_allocated >= $2
              RETURNING gpu_allocated
            )
            SELECT
              (SELECT gpu_allocated FROM dec) AS new_alloc,
              NOT EXISTS (
                SELECT 1 FROM model_deployments
                 WHERE target_node_id = $1
                   AND state IN ('PENDING_NODE','DEPLOYING','RUNNING')
              ) AS no_pending
            """
        if tx is not None:
            row = await tx.fetchrow(q, node_id, count)
        else:
            async with self.db.acquire() as conn:
                row = await conn.fetchrow(q, node_id, count)

        new_alloc = row["new_alloc"]
        if new_alloc is None:
            # Underflow defensive path.
            if tx is not None:
                cur = await tx.fetchval(
                    "SELECT gpu_allocated FROM compute_inventory WHERE id=$1",
                    node_id,
                )
            else:
                async with self.db.acquire() as conn:
                    cur = await conn.fetchval(
                        "SELECT gpu_allocated FROM compute_inventory WHERE id=$1",
                        node_id,
                    )
            logger.error(
                "refcount underflow: node=%s released=%d current=%s",
                node_id, count, cur,
            )
            return ReleaseResult(new_allocated=int(cur or 0),
                                  should_destroy=False)

        if new_alloc != 0:
            return ReleaseResult(new_allocated=new_alloc,
                                  should_destroy=False)
        return ReleaseResult(
            new_allocated=0,
            should_destroy=bool(row["no_pending"]),
        )

    async def purge_node(self, node_id, *, tx=None) -> None:
        """Hard-delete a node and ALL of its DB residue in one transaction.

        Today destroying an EC2 only SOFT-deletes the node (state set to
        'terminated'); the rows logically bound to it
        (``provisioning_jobs``, ``node_provisioning_events``,
        ``worker_bootstrap_tokens``, and bound ``model_deployments``)
        accumulate forever, because the inventory row is never hard-deleted.
        This is the authoritative cleanup that removes the lot.

        ``model_deployments.target_node_id`` is defined as
        ``ON DELETE NO ACTION`` (not a cascade), so any deployment row —
        whether active or already terminal — that still references this node
        will cause the step-5 ``DELETE FROM compute_inventory`` to raise a
        ``ForeignKeyViolationError``. Steps 1 and 1b clear those references
        before the inventory row is removed.

        Runs as ONE transaction (the caller's ``tx`` when supplied, else a
        freshly acquired connection + transaction) in this order:

          1. Fail+unbind any NON-terminal deployment still pointing at the
             node so it isn't left hanging on a node about to vanish.
          1b. Detach (NULL ``target_node_id`` only) any TERMINAL deployment
             still pointing at the node. Because ``model_deployments
             .target_node_id`` is ``ON DELETE NO ACTION``, even a terminal
             deployment's dangling reference blocks the step-5 hard-delete
             with a ForeignKeyViolationError. We deliberately do NOT touch
             the terminal deployment's state / endpoint / error_message:
             those record how it ended and must be preserved.
          2. DELETE node_provisioning_events (no FK on node_id — explicit
             delete required).
          3. DELETE worker_bootstrap_tokens (no FK on consumed_node_id —
             explicit delete required).
          4. DELETE provisioning_jobs (FK CASCADE on node_id, deleted
             explicitly so ordering is safe regardless of CASCADE).
          5. DELETE the compute_inventory row itself; the
             ``provisioning_jobs`` FK CASCADE has already been cleared by
             step 4. ``model_deployments`` references were nulled in steps
             1/1b (ON DELETE NO ACTION — no cascade fires).

        Idempotent and safe on a node with no residue / a nonexistent id —
        every statement is a no-op when nothing matches.

        Pass ``tx`` to run inside a caller's transaction.
        """

        async def _run(conn):
            await conn.execute(
                """
                UPDATE model_deployments
                   SET state = 'FAILED',
                       error_message = 'node deleted',
                       target_node_id = NULL,
                       endpoint = NULL
                 WHERE target_node_id = $1
                   AND state NOT IN ('TERMINATED', 'STOPPED', 'FAILED')
                """,
                node_id,
            )
            # Detach terminal deployments without altering their recorded
            # outcome, so the FK no longer pins the inventory row.
            await conn.execute(
                """
                UPDATE model_deployments
                   SET target_node_id = NULL
                 WHERE target_node_id = $1
                """,
                node_id,
            )
            await conn.execute(
                "DELETE FROM node_provisioning_events WHERE node_id = $1",
                node_id,
            )
            await conn.execute(
                "DELETE FROM worker_bootstrap_tokens WHERE consumed_node_id = $1",
                node_id,
            )
            await conn.execute(
                "DELETE FROM provisioning_jobs WHERE node_id = $1",
                node_id,
            )
            await conn.execute(
                "DELETE FROM compute_inventory WHERE id = $1",
                node_id,
            )

        if tx is not None:
            await _run(tx)
        else:
            async with self.db.acquire() as conn:
                async with conn.transaction():
                    await _run(conn)

    async def create_placeholder(
        self,
        *,
        pool_id: UUID,
        gpu_total: int,
        initial_alloc: int,
        agent_kind: str = "worker",
        group_id: str | None = None,
        tx=None,
    ) -> UUID:
        """Insert a state='provisioning' placeholder for a pool whose
        node hasn't booted yet.

        Pass ``group_id`` to assign the node to an Envoy proxy group.
        Nodes sharing the same group_id are load-balanced together.
        When set to ``str(pool_id)`` and every node in the pool carries
        it, the front Envoy collapses them into one cluster.

        Pass `tx` so this insert lands in the caller's transaction (T7's
        deploy_model uses this to atomically pair ColdStart + placeholder
        insert).
        """
        node_id = uuid4()
        q = """
            INSERT INTO compute_inventory(
              id, pool_id, provider, provider_instance_id, hostname,
              node_name, agent_kind, gpu_total, gpu_allocated,
              vcpu_total, vcpu_allocated, ram_gb_total, ram_gb_allocated,
              state, metadata, group_id
            )
            VALUES (
              $1, $2,
              (SELECT provider FROM compute_pools WHERE id=$2),
              $3, '', $4, $5,
              $6, $7, 0, 0, 0, 0, 'provisioning', '{}'::jsonb,
              $8
            )
            """
        args = (node_id, pool_id, f"placeholder:{node_id}",
                f"node-{node_id}", agent_kind, gpu_total, initial_alloc,
                group_id)
        if tx is not None:
            await tx.execute(q, *args)
        else:
            async with self.db.acquire() as conn:
                await conn.execute(q, *args)
        return node_id


# ---------------------------------------------------------------------------
# Worker-agent extensions (inferia-worker integration).
# ---------------------------------------------------------------------------


class DuplicateNodeError(Exception):
    """Raised by upsert_worker when (pool_id, node_name) is held by a row
    whose agent_kind is not 'worker'."""


async def _upsert_worker_impl(
    self, *, pool_id, node_name, advertise_url, allocatable, labels=None, group_id=None,
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
                provider, provider_instance_id, metadata, labels,
                group_id
            )
            VALUES ($1, $2, 'worker', 'provisioning', $3,
                    COALESCE(($4::jsonb ->> 'gpu')::int, 0),
                    COALESCE(($4::jsonb ->> 'cpu')::int, 0),
                    COALESCE(($4::jsonb ->> 'memory_gb')::int, 0),
                    'on_prem', $5, $4, $6::jsonb,
                    $7)
            ON CONFLICT (pool_id, node_name)
                WHERE agent_kind = 'worker' AND node_name IS NOT NULL
            DO UPDATE SET
                advertise_url = EXCLUDED.advertise_url,
                metadata = EXCLUDED.metadata,
                labels = compute_inventory.labels || EXCLUDED.labels,
                group_id = COALESCE(EXCLUDED.group_id, compute_inventory.group_id),
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
            RETURNING id, pool_id, node_name, agent_kind, state, advertise_url, group_id
            """,
            pool_id, node_name, advertise_url,
            __import__("json").dumps(allocatable),
            f"worker-{pool_id}-{node_name}",
            labels_json,
            group_id,
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


async def _update_heartbeat_with_telemetry_impl(
    self, *, node_id, used, loaded_models, deploy_metrics=None,
):
    """Persist heartbeat fields specific to the worker protocol."""
    payload = __import__("json").dumps({
        "used": used,
        "loaded_models": loaded_models,
    })
    # Per-deployment metrics stored separately under "deploy_metrics" key,
    # indexed by deployment_id so the getter can extract one deployment's data.
    dm_payload = None
    if deploy_metrics:
        dm_index: dict[str, dict] = {}
        for m in deploy_metrics:
            did = m.get("deployment_id")
            if did:
                dm_index[did] = m
        dm_payload = __import__("json").dumps({"deploy_metrics": dm_index})
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
        if dm_payload:
            await conn.execute(
                """
                UPDATE compute_inventory
                SET metadata = COALESCE(metadata, '{}'::jsonb) || $1::jsonb
                WHERE id = $2
                """,
                dm_payload, node_id,
            )


async def _get_deploy_metrics_impl(self, *, node_id, deployment_id):
    """Return the most recent deploy_metric for a specific deployment on a node."""
    async with self.db.acquire() as conn:
        row = await conn.fetchval(
            """
            SELECT metadata -> 'deploy_metrics' -> $1
            FROM compute_inventory
            WHERE id = $2
            """,
            deployment_id, node_id,
        )
    if row:
        return json.loads(row) if isinstance(row, str) else row
    return None


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
InventoryRepository.get_deploy_metrics = _get_deploy_metrics_impl
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
        "       i.hostname, i.created_at, i.updated_at,"
        "       i.group_id "
        "FROM compute_inventory i "
        "JOIN compute_pools p ON p.id = i.pool_id "
        # Match owner_id OR org_id: the legacy createpool path stores a user
        # id in owner_id while org_id holds the org UUID. Scoping on owner_id
        # alone hid a freshly-provisioned node from the dashboard ('the EC2
        # spins but doesn't show'). Matching either column surfaces it.
        "WHERE (p.owner_id = $1 OR p.org_id = $1) "
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
                   provider, provider_instance_id, created_at, updated_at,
                   group_id
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


async def _set_state_impl(self, *, node_id, state):
    """Set compute_inventory.state to an arbitrary node_state enum value.

    Used by the POST /provisioning/retry path (failed → provisioning) and
    the DELETE /nodes/{id} fallback path (terminal job → terminated).
    The caller is responsible for ensuring the value is a valid
    node_state enum member; invalid values surface as Postgres errors.
    Accepts node_id as either a str or a UUID for convenience.
    """
    nid = uuid.UUID(node_id) if isinstance(node_id, str) else node_id
    async with self.db.acquire() as conn:
        await conn.execute(
            """
            UPDATE compute_inventory
            SET state = $2, updated_at = now()
            WHERE id = $1
            """,
            nid, state,
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


async def _clear_terminating_node_impl(self, *, node_id):
    """Strip the ``terminating`` / ``terminating_at`` metadata flags from a
    node row.

    The inverse of :meth:`mark_terminating_node`. Used when a destroy that
    was *flagged* (``metadata.terminating='true'`` written ahead of the
    teardown) never actually went in flight — e.g. ``force_cancel`` raised
    and the destroy job was not enqueued. Without this the node would render
    "terminating" forever in the dashboard with no destroy actually running.
    The periodic reaper also re-arms the real teardown for such a node, but
    clearing the flag here avoids a misleading UI in the interim.

    Idempotent: a row with neither key set, or a nonexistent id, is a no-op.
    Accepts ``node_id`` as either a str or a UUID for convenience.
    """
    nid = uuid.UUID(node_id) if isinstance(node_id, str) else node_id
    async with self.db.acquire() as conn:
        await conn.execute(
            """
            UPDATE compute_inventory
            SET metadata = (COALESCE(metadata, '{}'::jsonb)
                            - 'terminating' - 'terminating_at'),
                updated_at = now()
            WHERE id = $1
            """,
            nid,
        )


InventoryRepository.list_nodes = _list_nodes_impl
InventoryRepository.get_node = _get_node_impl
InventoryRepository.set_labels = _set_labels_impl
InventoryRepository.soft_delete_node = _soft_delete_node_impl
InventoryRepository.mark_terminating_node = _mark_terminating_node_impl
InventoryRepository.clear_terminating_node = _clear_terminating_node_impl
InventoryRepository.set_state = _set_state_impl

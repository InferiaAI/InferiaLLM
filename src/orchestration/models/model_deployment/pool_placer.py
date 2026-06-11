"""Pool placement: bind or provision.

PoolPlacer decides where to put an incoming deployment. It returns one
of four cases:

  - BindToReady(node_id):        an existing 'ready' node has capacity.
  - CoWaitOnProvisioning(node_id): an in-flight 'provisioning' placeholder
                                    has capacity; deploy will wait until
                                    the node finishes booting.
  - ColdStart(spec):              no existing slot fits; caller should
                                    create a placeholder and enqueue a
                                    ProvisioningJob (or, for worker pools,
                                    just wait for a registration).
  - raises PoolAtCapacity:        compute_pools.max_nodes was reached and
                                    no existing node has capacity.

The single SQL query inspects both 'ready' and 'provisioning' rows.
Ready wins ties (warm bind > cold co-wait). Within a state, best-fit
ordering by smallest free GPU count packs concurrent deploys tightly.
FOR UPDATE SKIP LOCKED prevents two concurrent placers from picking
the same row.
"""
from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

import asyncpg


@dataclass(frozen=True)
class BindToReady:
    node_id: UUID


@dataclass(frozen=True)
class CoWaitOnProvisioning:
    node_id: UUID


@dataclass(frozen=True)
class ColdStart:
    gpu_total_per_node: int
    provider: str


class PoolAtCapacity(Exception):
    def __init__(self, *, current_nodes: int, max_nodes: int) -> None:
        super().__init__(
            f"pool at capacity: {current_nodes}/{max_nodes} nodes used"
        )
        self.current_nodes = current_nodes
        self.max_nodes = max_nodes


PlacementDecision = BindToReady | CoWaitOnProvisioning | ColdStart


class PoolPlacer:
    def __init__(self, db: asyncpg.Pool) -> None:
        self.db = db

    async def place(
        self,
        *,
        pool_id: UUID,
        gpu_required: int,
        tx=None,
    ) -> PlacementDecision:
        """Decide where to put an incoming deployment.

        When `tx` is provided, both the candidate SELECT and the
        pool-meta SELECT run on the caller's transaction. The
        FOR UPDATE SKIP LOCKED row lock then lives until the caller
        commits — letting T7's deploy_model atomically combine
        place() + create_placeholder() + bind so two concurrent
        cold-start deploys produce ONE placeholder, not two.

        When `tx` is None, a fresh transaction is opened for the
        duration of this call only. Safe for non-concurrent callers.

        Raises:
            PoolAtCapacity: when pool.max_nodes is set and
                current_nodes >= max_nodes.
            ValueError: when pool_id does not match any row in
                compute_pools.
        """
        if tx is not None:
            return await self._place_in_tx(tx, pool_id, gpu_required)
        async with self.db.acquire() as conn:
            async with conn.transaction():
                return await self._place_in_tx(conn, pool_id, gpu_required)

    async def _place_in_tx(
        self,
        conn,
        pool_id: UUID,
        gpu_required: int,
    ) -> PlacementDecision:
        # 1. Try to bind onto an existing node (ready preferred,
        #    provisioning accepted, both must have capacity).
        row = await conn.fetchrow(
            """
            SELECT id, state
              FROM compute_inventory
             WHERE pool_id = $1
               AND state IN ('ready', 'provisioning')
               AND (metadata->>'terminating') IS DISTINCT FROM 'true'
               AND gpu_total - gpu_allocated >= $2
          ORDER BY (CASE WHEN state = 'ready' THEN 0 ELSE 1 END),
                   (gpu_total - gpu_allocated) ASC
             LIMIT 1
               FOR UPDATE SKIP LOCKED
            """,
            pool_id, gpu_required,
        )
        if row is not None:
            if row["state"] == "ready":
                return BindToReady(node_id=row["id"])
            return CoWaitOnProvisioning(node_id=row["id"])

        # 2. No slot — would adding a new node exceed max_nodes?
        pool_meta = await conn.fetchrow(
            """
            SELECT p.provider,
                   p.gpu_count,
                   p.max_nodes,
                   (SELECT COUNT(*) FROM compute_inventory ci
                     WHERE ci.pool_id = p.id
                       AND ci.state IN ('ready', 'provisioning')
                       AND (ci.metadata->>'terminating') IS DISTINCT FROM 'true'
                   ) AS current_nodes
              FROM compute_pools p
             WHERE p.id = $1
            """,
            pool_id,
        )
        if pool_meta is None:
            raise ValueError(f"pool not found: {pool_id}")

        max_nodes = pool_meta["max_nodes"]
        current = int(pool_meta["current_nodes"])
        if max_nodes is not None and current >= max_nodes:
            raise PoolAtCapacity(
                current_nodes=current,
                max_nodes=max_nodes,
            )

        return ColdStart(
            gpu_total_per_node=int(pool_meta["gpu_count"]),
            provider=str(pool_meta["provider"]),
        )

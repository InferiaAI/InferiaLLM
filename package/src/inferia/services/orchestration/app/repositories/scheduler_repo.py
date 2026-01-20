from uuid import UUID
from datetime import datetime, timezone

class SchedulerRepository:
    def __init__(self, db, quota_repo):
        self.db = db
        self.quota_repo = quota_repo

    # =================================================
    # SIMPLE ALLOCATION (NO PREEMPTION)
    # =================================================
    async def allocate(
        self,
        *,
        allocation_id: UUID,
        node_id: UUID,
        gpu: int,
        vcpu: int,
        ram_gb: int,
        priority: int,
        owner_type: str,
        owner_id: str,
    ):
        async with self.db.acquire() as conn:
            async with conn.transaction():

                await self.quota_repo.ensure_owner(
                    owner_type, owner_id, conn
                )

                ok, reason = await self.quota_repo.check_hard_limits(
                    owner_type, owner_id, gpu, vcpu, ram_gb, conn
                )
                if not ok:
                    return False, reason, None

                node = await self._lock_node(conn, node_id)
                if not node:
                    return False, "NODE_NOT_READY", None

                if not self._has_capacity(node, gpu, vcpu, ram_gb):
                    return False, "INSUFFICIENT_CAPACITY", node["pool_id"]

                await self._commit_allocation(
                    conn=conn,
                    allocation_id=allocation_id,
                    node=node,
                    gpu=gpu,
                    vcpu=vcpu,
                    ram_gb=ram_gb,
                    priority=priority,
                    owner_type=owner_type,
                    owner_id=owner_id,
                )

                return True, "ALLOCATED", node["pool_id"]

    # =================================================
    # PREEMPTIVE ALLOCATION
    # =================================================
    async def allocate_with_preemption(
        self,
        *,
        allocation_id: UUID,
        node_id: UUID,
        gpu: int,
        vcpu: int,
        ram_gb: int,
        priority: int,
        owner_type: str,
        owner_id: str,
    ):
        async with self.db.acquire() as conn:
            async with conn.transaction():

                await self.quota_repo.ensure_owner(
                    owner_type, owner_id, conn
                )

                ok, reason = await self.quota_repo.check_hard_limits(
                    owner_type, owner_id, gpu, vcpu, ram_gb, conn
                )
                if not ok:
                    return False, reason, None

                node = await self._lock_node(conn, node_id)
                if not node:
                    return False, "NODE_NOT_READY", None

                free_gpu = node["gpu_total"] - node["gpu_allocated"]
                free_vcpu = node["vcpu_total"] - node["vcpu_allocated"]
                free_ram = node["ram_gb_total"] - node["ram_gb_allocated"]

                # ---------- FAST PATH ----------
                if free_gpu >= gpu and free_vcpu >= vcpu and free_ram >= ram_gb:
                    await self._commit_allocation(
                        conn,
                        allocation_id,
                        node,
                        gpu,
                        vcpu,
                        ram_gb,
                        priority,
                        owner_type,
                        owner_id,
                    )
                    return True, "ALLOCATED", node["pool_id"]

                # ---------- PREEMPTION PATH ----------
                victims = await conn.fetch(
                    """
                    SELECT allocation_id, gpu, vcpu, ram_gb
                    FROM allocations
                    WHERE node_id=$1
                      AND preemptible=TRUE
                      AND priority < $2
                      AND released_at IS NULL
                    ORDER BY priority ASC, created_at ASC
                    FOR UPDATE
                    """,
                    node_id,
                    priority,
                )

                reclaimed_gpu = free_gpu
                reclaimed_vcpu = free_vcpu
                reclaimed_ram = free_ram
                evict = []

                for v in victims:
                    reclaimed_gpu += v["gpu"]
                    reclaimed_vcpu += v["vcpu"]
                    reclaimed_ram += v["ram_gb"]
                    evict.append(v)

                    if (
                        reclaimed_gpu >= gpu and
                        reclaimed_vcpu >= vcpu and
                        reclaimed_ram >= ram_gb
                    ):
                        break

                if reclaimed_gpu < gpu or reclaimed_vcpu < vcpu or reclaimed_ram < ram_gb:
                    return False, "INSUFFICIENT_CAPACITY", node["pool_id"]

                # ---- EXECUTE PREEMPTION ----
                for v in evict:
                    await self._release_allocation_locked(
                        conn,
                        allocation_id=v["allocation_id"],
                        node_id=node_id,
                        gpu=v["gpu"],
                        vcpu=v["vcpu"],
                        ram_gb=v["ram_gb"],
                    )

                await self._commit_allocation(
                    conn,
                    allocation_id,
                    node,
                    gpu,
                    vcpu,
                    ram_gb,
                    priority,
                    owner_type,
                    owner_id,
                )

                return True, "PREEMPTED_AND_ALLOCATED", node["pool_id"]

    # =================================================
    # RELEASE
    # =================================================
    async def release(self, *, allocation_id: UUID):
        async with self.db.acquire() as conn:
            async with conn.transaction():

                row = await conn.fetchrow(
                    """
                    SELECT allocation_id, node_id, gpu, vcpu, ram_gb
                    FROM allocations
                    WHERE allocation_id=$1 AND released_at IS NULL
                    FOR UPDATE
                    """,
                    allocation_id,
                )

                if not row:
                    return False

                await self._release_allocation_locked(
                    conn,
                    allocation_id=row["allocation_id"],
                    node_id=row["node_id"],
                    gpu=row["gpu"],
                    vcpu=row["vcpu"],
                    ram_gb=row["ram_gb"],
                )

                return True

    # =================================================
    # INTERNAL HELPERS
    # =================================================
    async def _lock_node(self, conn, node_id: UUID):
        return await conn.fetchrow(
            """
            SELECT id, pool_id,
                   gpu_total, gpu_allocated,
                   vcpu_total, vcpu_allocated,
                   ram_gb_total, ram_gb_allocated,
                   node_class
            FROM compute_inventory
            WHERE id=$1 AND state='ready'
            FOR UPDATE
            """,
            node_id,
        )

    def _has_capacity(self, node, gpu, vcpu, ram):
        return (
            node["gpu_total"] - node["gpu_allocated"] >= gpu and
            node["vcpu_total"] - node["vcpu_allocated"] >= vcpu and
            node["ram_gb_total"] - node["ram_gb_allocated"] >= ram
        )

    async def _commit_allocation(
        self,
        conn,
        allocation_id,
        node,
        gpu,
        vcpu,
        ram_gb,
        priority,
        owner_type,
        owner_id,
    ):
        preemptible = node["node_class"] == "spot"

        await conn.execute(
            """
            UPDATE compute_inventory
            SET
              gpu_allocated = gpu_allocated + $2,
              vcpu_allocated = vcpu_allocated + $3,
              ram_gb_allocated = ram_gb_allocated + $4
            WHERE id=$1
            """,
            node["id"],
            gpu,
            vcpu,
            ram_gb,
        )

        await conn.execute(
            """
            INSERT INTO allocations (
                allocation_id,
                node_id,
                gpu,
                vcpu,
                ram_gb,
                priority,
                preemptible,
                owner_type,
                owner_id,
                node_class,
                created_at
            )
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
            """,
            allocation_id,
            node["id"],
            gpu,
            vcpu,
            ram_gb,
            priority,
            preemptible,
            owner_type,
            owner_id,
            node["node_class"],
            datetime.now(timezone.utc),
        )

    async def _release_allocation_locked(
        self,
        conn,
        *,
        allocation_id,
        node_id,
        gpu,
        vcpu,
        ram_gb,
    ):
        await conn.execute(
            """
            UPDATE compute_inventory
            SET
              gpu_allocated = gpu_allocated - $2,
              vcpu_allocated = vcpu_allocated - $3,
              ram_gb_allocated = ram_gb_allocated - $4
            WHERE id=$1
            """,
            node_id,
            gpu,
            vcpu,
            ram_gb,
        )

        await conn.execute(
            """
            UPDATE allocations
            SET released_at = $2
            WHERE allocation_id = $1
            """,
            allocation_id,
            datetime.now(timezone.utc),
        )

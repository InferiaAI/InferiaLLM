class QuotaRepository:
    def __init__(self, db):
        self.db = db

    async def ensure_owner(self, owner_type, owner_id, conn):
        await conn.execute(
            """
            INSERT INTO usage_snapshot (owner_type, owner_id)
            VALUES ($1,$2)
            ON CONFLICT DO NOTHING
            """,
            owner_type, owner_id
        )

    async def check_hard_limits(
        self, owner_type, owner_id, gpu, vcpu, ram, conn
    ):
        row = await conn.fetchrow(
            """
            SELECT q.max_gpu, q.max_vcpu, q.max_ram_gb, q.max_allocations,
                   u.gpu_in_use, u.vcpu_in_use, u.ram_gb_in_use, u.allocations
            FROM quotas q
            JOIN usage_snapshot u
              ON q.owner_type=u.owner_type AND q.owner_id=u.owner_id
            WHERE q.owner_type=$1 AND q.owner_id=$2
            FOR UPDATE
            """,
            owner_type, owner_id
        )

        if not row:
            return True, None  # no quotas defined

        if row["max_gpu"] is not None and row["gpu_in_use"] + gpu > row["max_gpu"]:
            return False, "GPU_QUOTA_EXCEEDED"

        if row["max_vcpu"] is not None and row["vcpu_in_use"] + vcpu > row["max_vcpu"]:
            return False, "VCPU_QUOTA_EXCEEDED"

        if row["max_ram_gb"] is not None and row["ram_gb_in_use"] + ram > row["max_ram_gb"]:
            return False, "RAM_QUOTA_EXCEEDED"

        if row["max_allocations"] is not None and row["allocations"] + 1 > row["max_allocations"]:
            return False, "ALLOCATION_QUOTA_EXCEEDED"

        return True, None

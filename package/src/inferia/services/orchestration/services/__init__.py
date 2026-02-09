# async def allocate(self, allocation_id, node_id, gpu, vcpu, ram_gb):
#     async with self.db.acquire() as conn:
#         async with conn.transaction():
#             row = await conn.fetchrow(
#                 """
#                 SELECT pool_id,
#                        gpu_total, gpu_allocated,
#                        vcpu_total, vcpu_allocated,
#                        ram_gb_total, ram_gb_allocated
#                 FROM compute_inventory
#                 WHERE id=$1 AND state='ready'
#                 FOR UPDATE
#                 """,
#                 node_id
#             )

#             # ❗ FIX 1: always return 3 values
#             if not row:
#                 return False, "NODE_NOT_READY", None

#             pool_id = row["pool_id"]

#             if (
#                 row["gpu_total"] - row["gpu_allocated"] < gpu or
#                 row["vcpu_total"] - row["vcpu_allocated"] < vcpu or
#                 row["ram_gb_total"] - row["ram_gb_allocated"] < ram_gb
#             ):
#                 # ❗ FIX 2: still return pool_id
#                 return False, "INSUFFICIENT_CAPACITY", pool_id

#             # allocation success
#             await conn.execute(
#                 """
#                 UPDATE compute_inventory
#                 SET
#                   gpu_allocated = gpu_allocated + $2,
#                   vcpu_allocated = vcpu_allocated + $3,
#                   ram_gb_allocated = ram_gb_allocated + $4
#                 WHERE id=$1
#                 """,
#                 node_id, gpu, vcpu, ram_gb
#             )

#             return True, "ALLOCATED", pool_id

from uuid import UUID
from datetime import datetime, timedelta, timezone
import grpc
from inferia.services.orchestration.v1 import compute_pool_pb2, compute_pool_pb2_grpc


class ComputePoolManagerService(compute_pool_pb2_grpc.ComputePoolManagerServicer):
    def __init__(self, repo):
        self.repo = repo

    async def RegisterPool(self, request, context):
        try:
            pool_data = {
                "pool_name": request.pool_name,
                "owner_type": request.owner_type,
                "owner_id": request.owner_id,
                "provider": request.provider,
                "allowed_gpu_types": list(request.allowed_gpu_types),
                "max_cost_per_hour": request.max_cost_per_hour,
                "is_dedicated": request.is_dedicated,
                "provider_pool_id": request.provider_pool_id,
                "scheduling_policy": (
                    request.scheduling_policy_json or '{"strategy":"best_fit"}'
                ),
            }

            # Include provider_credential_name if provided (works for any provider: nosana, akash, etc.)
            if request.provider_credential_name:
                pool_data["provider_credential_name"] = request.provider_credential_name

            pool_id = await self.repo.create_pool(pool_data)

            return compute_pool_pb2.PoolResponse(
                pool_id=str(pool_id),
                pool_name=request.pool_name,
                provider=request.provider,
                is_active=True,
            )
        except Exception as e:
            # Check for asyncpg unique violation by string name or specific type if imported
            # Since we don't want to depend tightly on asyncpg here if not necessary,
            # we can inspect the exception type name or just import it.
            # However, for stability, let's assume asyncpg is available as the repo uses it.
            if "UniqueViolationError" in type(e).__name__:
                context.abort(
                    grpc.StatusCode.ALREADY_EXISTS,
                    f"Pool '{request.pool_name}' already exists for this owner.",
                )
            raise e

    async def UpdatePool(self, request, context):
        await self.repo.update_pool(
            UUID(request.pool_id),
            {
                "allowed_gpu_types": list(request.allowed_gpu_types),
                "max_cost_per_hour": request.max_cost_per_hour,
                "is_dedicated": request.is_dedicated,
            },
        )

        return compute_pool_pb2.PoolResponse(pool_id=request.pool_id, is_active=True)

    async def DeletePool(self, request, context):
        await self.repo.soft_delete_pool(UUID(request.pool_id))
        return compute_pool_pb2.poolEmpty()

    async def BindProviderResource(self, request, context):
        await self.repo.bind_provider_resource(
            UUID(request.pool_id),
            UUID(request.provider_resource_id),
            request.priority or 100,
        )
        return compute_pool_pb2.poolEmpty()

    async def UnbindProviderResource(self, request, context):
        await self.repo.unbind_provider_resource(
            UUID(request.pool_id),
            UUID(request.provider_resource_id),
        )
        return compute_pool_pb2.poolEmpty()

    async def ListPoolInventory(self, request, context):
        def utcnow_naive():
            return datetime.now(timezone.utc).replace(tzinfo=None)

        rows = await self.repo.list_pool_inventory(UUID(request.pool_id))
        now = utcnow_naive()
        filtered_nodes = []
        for r in rows:
            check_time = r["last_heartbeat"] or r["created_at"]
            if check_time:
                # Ensure hb is naive for comparison with now
                hb = check_time
                if hb.tzinfo is not None:
                    hb = hb.replace(tzinfo=None)

                if (now - hb) > timedelta(minutes=2):
                    continue

            if r["state"] == "terminated":
                continue

            filtered_nodes.append(
                compute_pool_pb2.InventoryNode(
                    node_id=str(r["node_id"]),
                    provider=r["provider"],
                    state=r["state"],
                    gpu_total=r["gpu_total"] or 0,
                    gpu_allocated=r["gpu_allocated"] or 0,
                    vcpu_total=r["vcpu_total"] or 0,
                    vcpu_allocated=r["vcpu_allocated"] or 0,
                    expose_url=r["expose_url"] or "",
                )
            )

        return compute_pool_pb2.ListPoolInventoryResponse(nodes=filtered_nodes)

    async def ListPools(self, request, context):
        rows = await self.repo.list_pools(owner_id=request.owner_id or None)

        return compute_pool_pb2.ListPoolsResponse(
            pools=[
                compute_pool_pb2.PoolResponse(
                    pool_id=str(row["id"]),
                    pool_name=row["pool_name"],
                    provider=row["provider"],
                    is_active=row["is_active"],
                )
                for row in rows
            ]
        )

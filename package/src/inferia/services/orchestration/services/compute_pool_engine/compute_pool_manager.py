from uuid import UUID
from datetime import datetime, timedelta, timezone
import grpc
import logging
import uuid as uuid_module
import asyncio
from typing import Optional
from inferia.services.orchestration.v1 import compute_pool_pb2, compute_pool_pb2_grpc
from inferia.services.orchestration.services.adapter_engine.registry import get_adapter

logger = logging.getLogger(__name__)


class ComputePoolManagerService(compute_pool_pb2_grpc.ComputePoolManagerServicer):
    def __init__(self, repo, deployment_repo=None, controller=None):
        self.repo = repo
        self.deployment_repo = deployment_repo
        self.controller = controller

    async def RegisterPool(self, request, context):
        # Determine pool type based on provider capabilities
        adapter = None
        capabilities = None
        try:
            adapter = get_adapter(request.provider)
            capabilities = adapter.get_capabilities()
        except ValueError:
            logger.warning(f"No adapter found for provider '{request.provider}'")

        pool_type = (
            "cluster" if capabilities and capabilities.supports_cluster_mode else "job"
        )

        # Check if pool already exists (to prevent long provisioning for a duplicate)
        existing_pools = await self.repo.list_pools(owner_id=request.owner_id)
        for p in existing_pools:
            if p["pool_name"] == request.pool_name and p["owner_type"] == request.owner_type:
                await context.abort(
                    grpc.StatusCode.ALREADY_EXISTS,
                    f"Pool '{request.pool_name}' already exists for this owner.",
                )
                return

        region_constraint = (
            list(request.region_constraint) if request.region_constraint else []
        )
        use_spot = request.use_spot if hasattr(request, "use_spot") else False
        gpu_count = request.gpu_count if hasattr(request, "gpu_count") and request.gpu_count > 0 else 1

        pool_data = {
            "pool_name": request.pool_name,
            "owner_type": request.owner_type,
            "owner_id": request.owner_id,
            "provider": request.provider,
            "pool_type": pool_type,
            "allowed_gpu_types": list(request.allowed_gpu_types),
            "max_cost_per_hour": request.max_cost_per_hour,
            "is_dedicated": request.is_dedicated,
            "provider_pool_id": request.provider_pool_id,
            "scheduling_policy": (
                request.scheduling_policy_json or '{"strategy":"best_fit"}'
            ),
            "region_constraint": region_constraint,
            "gpu_count": gpu_count,
        }

        if request.provider_credential_name:
            credential_exists = await self.repo.credential_exists(
                request.provider, request.provider_credential_name
            )
            if not credential_exists:
                await context.abort(
                    grpc.StatusCode.FAILED_PRECONDITION,
                    f"Credential '{request.provider_credential_name}' not found or inactive for provider '{request.provider}'",
                )
                return
            pool_data["provider_credential_name"] = request.provider_credential_name

        # Create pool in DB FIRST
        # Create pool in DB FIRST
        # is_active=True means 'exists/not deleted' in this system
        pool_data["is_active"] = True
        pool_data["lifecycle_state"] = "running"

        try:
            pool_id = await self.repo.create_pool(pool_data)
        except Exception as e:
            if "UniqueViolationError" in type(e).__name__:
                await context.abort(
                    grpc.StatusCode.ALREADY_EXISTS,
                    f"Pool '{request.pool_name}' already exists for this owner.",
                )
                return
            raise e

        # Handle provisioning
        if (
            pool_type == "cluster"
            and adapter
            and capabilities
            and capabilities.supports_cluster_mode
        ):
            # Background the provisioning for cluster-based providers
            asyncio.create_task(
                self._provision_cluster_task(
                    pool_id=pool_id,
                    pool_name=request.pool_name,
                    adapter=adapter,
                    gpu_type=(
                        request.allowed_gpu_types[0]
                        if request.allowed_gpu_types
                        else "A100"
                    ),
                    gpu_count=gpu_count,
                    region=region_constraint[0] if region_constraint else None,
                    use_spot=use_spot,
                    provider_name=request.provider,
                    provider_credential_name=request.provider_credential_name,
                )
            )

            # Return immediately for clusters
            return compute_pool_pb2.PoolResponse(
                pool_id=str(pool_id),
                pool_name=request.pool_name,
                provider=request.provider,
                is_active=True,
            )

        # For job-based providers, we are done
        return compute_pool_pb2.PoolResponse(
            pool_id=str(pool_id),
            pool_name=request.pool_name,
            provider=request.provider,
            is_active=True,
        )

    async def _provision_cluster_task(
        self,
        pool_id: uuid_module.UUID,
        pool_name: str,
        adapter,
        gpu_type: str,
        gpu_count: int,
        region: Optional[str],
        use_spot: bool,
        provider_name: str,
        provider_credential_name: Optional[str],
    ):
        """Background task to provision a cluster and update the pool record."""
        try:
            logger.info(f"Provisioning cluster for pool '{pool_name}' (ID: {pool_id}), gpu_count={gpu_count}")

            # Load provider config from DB and apply credentials
            db_config = await self.repo.get_provider_config(provider_name)
            if db_config and hasattr(adapter, 'apply_config'):
                adapter.apply_config(db_config)

            cluster_name = f"inferia-{provider_name}-{uuid_module.uuid4().hex[:8]}"

            cluster_info = await adapter.provision_cluster(
                cluster_name=cluster_name,
                gpu_type=gpu_type,
                gpu_count=gpu_count,
                region=region,
                use_spot=use_spot,
                provider_credential_name=provider_credential_name,
            )

            cluster_id = cluster_info["cluster_id"]
            await self.repo.update_pool_cluster_id(pool_id, cluster_id)
            # await self.repo.set_pool_active(pool_id, True) # Already True

            logger.info(
                f"Cluster '{cluster_id}' provisioned successfully for pool '{pool_name}'"
            )

        except Exception as e:
            logger.error(f"Failed to provision cluster for pool '{pool_name}': {e}")
            # We don't mark as inactive here for now, but we could add a state field later

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

    async def GetPool(self, request, context):
        row = await self.repo.get(UUID(request.pool_id))
        if not row:
            context.abort(
                grpc.StatusCode.NOT_FOUND, f"Pool '{request.pool_id}' not found"
            )
            return

        return compute_pool_pb2.PoolResponse(
            pool_id=str(row["id"]),
            pool_name=row["pool_name"],
            provider=row["provider"],
            pool_type=row.get("pool_type", "job"),
            is_active=row["is_active"],
            owner_type=row["owner_type"],
            owner_id=row["owner_id"],
            allowed_gpu_types=row["allowed_gpu_types"] or [],
            max_cost_per_hour=row["max_cost_per_hour"],
            is_dedicated=row["is_dedicated"],
            scheduling_policy_json=row["scheduling_policy"] or "",
            provider_pool_id=row["provider_pool_id"] or "",
            provider_credential_name=row["provider_credential_name"] or "",
            cluster_id=row.get("cluster_id") or "",
            created_at=row["created_at"].isoformat() if row["created_at"] else "",
            updated_at=row["updated_at"].isoformat() if row["updated_at"] else "",
            gpu_count=row.get("gpu_count") or 1,
        )

    async def DeletePool(self, request, context):
        pool_id = UUID(request.pool_id)

        # Final delete is only allowed after explicit stop -> terminated lifecycle.
        pool = await self.repo.get(pool_id)
        if not pool:
            await context.abort(
                grpc.StatusCode.NOT_FOUND, f"Pool '{request.pool_id}' not found"
            )
            return

        lifecycle_state = pool.get("lifecycle_state") or "running"
        if lifecycle_state != "terminated":
            await context.abort(
                grpc.StatusCode.FAILED_PRECONDITION,
                f"Pool '{request.pool_id}' is '{lifecycle_state}'. Stop it first.",
            )
            return

        # Cascade cleanup: Delete/Terminate deployments in this pool
        if self.deployment_repo:
            try:
                deployments = await self.deployment_repo.list(pool_id=pool_id)
                for dep in deployments:
                    dep_id = dep["deployment_id"]
                    if dep["state"] in ("STOPPED", "TERMINATED", "FAILED", "PENDING"):
                        await self.deployment_repo.delete(dep_id)
                    elif self.controller:
                        try:
                            await self.controller.request_delete(dep_id)
                        except Exception:
                            pass
            except Exception as e:
                logger.error(
                    f"Error during deployment cascade cleanup for pool {pool_id}: {e}"
                )

        await self.repo.soft_delete_pool(pool_id)
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

        pool_id = UUID(request.pool_id)
        rows = await self.repo.list_pool_inventory(pool_id)

        # Cluster-based pools don't have heartbeat agents, skip staleness check
        pool = await self.repo.get(pool_id)
        is_cluster_pool = pool and pool.get("pool_type") == "cluster"

        now = utcnow_naive()
        filtered_nodes = []
        for r in rows:
            if not is_cluster_pool:
                check_time = r["last_heartbeat"] or r["created_at"]
                if check_time:
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
                    pool_type=row.get("pool_type", "job"),
                    is_active=row["is_active"],
                    owner_type=row["owner_type"],
                    owner_id=row["owner_id"],
                    allowed_gpu_types=row["allowed_gpu_types"] or [],
                    max_cost_per_hour=row["max_cost_per_hour"],
                    is_dedicated=row["is_dedicated"],
                    scheduling_policy_json=row["scheduling_policy"] or "",
                    provider_pool_id=row["provider_pool_id"] or "",
                    provider_credential_name=row["provider_credential_name"] or "",
                    cluster_id=row.get("cluster_id") or "",
                    created_at=row["created_at"].isoformat()
                    if row["created_at"]
                    else "",
                    updated_at=row["updated_at"].isoformat()
                    if row["updated_at"]
                    else "",
                    gpu_count=row.get("gpu_count") or 1,
                )
                for row in rows
            ]
        )

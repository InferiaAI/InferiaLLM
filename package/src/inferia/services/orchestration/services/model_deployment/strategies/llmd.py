class LLMdDeploymentStrategy:
    def __init__(
        self,
        placement_engine,
        scheduler_engine,
        llmd_runtime,
    ):
        self.placement = placement_engine
        self.scheduler = scheduler_engine
        self.llmd = llmd_runtime

    async def deploy(
        self,
        *,
        deployment_id,
        model,
        pool_id,
        replicas,
        gpu_per_replica,
        workload_type,
    ):
        node_ids = []
        node_names = []
        allocations = []

        for _ in range(replicas):
            placement = await self.placement.place_workload(
                pool_id=pool_id,
                gpu_required=gpu_per_replica,
                vcpu_required=2,
                ram_gb_required=8,
                workload_type=workload_type,
            )
            node_ids.append(placement.node_id)
            node_names.append(str(placement.node_id))

        for node_id in node_ids:
            alloc = await self.scheduler.allocate(
                node_id=node_id,
                gpu=gpu_per_replica,
                vcpu=2,
                ram_gb=8,
                priority=1000,
                owner_type="deployment",
                owner_id=str(deployment_id),
            )
            allocations.append(alloc.allocation_id)

        runtime = await self.llmd.deploy(
            deployment_id=deployment_id,
            model=model,
            replicas=replicas,
            gpu_per_replica=gpu_per_replica,
            node_names=node_names,
        )

        return {
            "allocations": allocations,
            **runtime,
        }

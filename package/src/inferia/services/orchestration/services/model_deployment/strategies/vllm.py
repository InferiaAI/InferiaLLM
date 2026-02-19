# services/model_deployment/strategies/vllm.py
import uuid

class VLLMDeploymentStrategy:
    """
    VLLM deployment strategy.

    Guarantees:
    - Single-replica enforcement
    - Atomic allocation
    - Deterministic rollback
    - No runtime side-effects
    """

    def __init__(
        self,
        scheduler_repo,
    ):
        self.scheduler = scheduler_repo

    async def deploy(
        self,
        *,
        deployment_id,
        model,
        pool_id,
        node_id,
        replicas,
        gpu_per_replica,
        vcpu_per_replica,
        ram_gb_per_replica,
        workload_type,
    ):
        # ------------------------------------------------
        # HARD VALIDATION (must happen BEFORE orchestration)
        # ------------------------------------------------
        if replicas != 1:
            raise ValueError(
                "vLLM supports only single-replica deployments"
            )

        allocation_id = uuid.uuid4()
        allocated = False

        try:
            # ------------------------------------------------
            # ATOMIC RESOURCE ACQUISITION (NO PLACEMENT CALL)
            # ------------------------------------------------
            ok, reason, _ = await self.scheduler.allocate(
                allocation_id=allocation_id,
                node_id=node_id,
                gpu=gpu_per_replica,
                vcpu=vcpu_per_replica,
                ram_gb=ram_gb_per_replica,
                owner_type="deployment",
                owner_id=str(deployment_id),
                priority=100,
            )

            if not ok:
                raise RuntimeError(f"Allocation failed: {reason} (node: {node_id})")

            allocated = True

            # ------------------------------------------------
            # RETURN *DESIRED STATE ONLY*
            # Runtime execution happens in WORKER
            # ------------------------------------------------
            return {
                "node_ids": [node_id],
                "allocation_ids": [allocation_id],
                "runtime": "vllm",
                "desired_state": "DEPLOY",
                "model": model,
            }

        except Exception:
            # ------------------------------------------------
            # ROLLBACK GUARANTEE (NO ZOMBIES)
            # ------------------------------------------------
            if allocated:
                await self.scheduler.release(
                    allocation_id=allocation_id
                )
            raise


    async def terminate(
        self,
        *,
        deployment_id,
        allocation_ids,
        node_ids,
        llmd_resource_name,
        runtime,
    ):
        # ------------------------------------------------
        # RETURN *DESIRED STATE ONLY*
        # Runtime termination happens in WORKER
        # ------------------------------------------------
        return {
            "desired_state": "TERMINATE",
        }

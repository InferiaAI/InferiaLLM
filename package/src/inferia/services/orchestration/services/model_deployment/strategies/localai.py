# services/model_deployment/strategies/localai.py
"""
LocalAI deployment strategy for image generation models.

Supports Stable Diffusion and other image models via LocalAI backend.
See: https://localai.io/features/image-generation/

LocalAI runs as a single container with models loaded at startup.
Unlike vLLM (which needs GPU scheduling per-model), LocalAI can serve
multiple modalities from one instance. This strategy handles:
  - Single-replica deployment
  - Atomic GPU allocation
  - Deterministic rollback
"""

import uuid


class LocalAIDeploymentStrategy:
    """
    LocalAI deployment strategy for image generation workloads.

    Guarantees:
    - Single-replica enforcement (LocalAI runs one server per node)
    - Atomic allocation
    - Deterministic rollback
    - No runtime side-effects
    """

    def __init__(self, scheduler_repo):
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
        # HARD VALIDATION
        # ------------------------------------------------
        if replicas != 1:
            raise ValueError(
                "LocalAI supports only single-replica deployments"
            )

        allocation_id = uuid.uuid4()
        allocated = False

        try:
            # ------------------------------------------------
            # ATOMIC RESOURCE ACQUISITION
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
                "runtime": "localai",
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
        llmd_resource_name=None,
        runtime,
    ):
        return {
            "desired_state": "TERMINATE",
        }

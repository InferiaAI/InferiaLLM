"""
Direct-managed GPU worker deployment strategy.

Used when the model_deployment service routes a deployment to a node whose
``agent_kind='worker'``. The caller (ModelDeploymentWorker) has already
picked ``node_id`` and validated the model spec; this strategy is
responsible for atomic resource allocation + delegating the actual
container launch to the connected worker over the WS control channel.

Mirrors the signature of VLLMDeploymentStrategy so it slots into the same
``runtime_strategies`` dict in worker_main.py.
"""

from __future__ import annotations

import uuid


class WorkerDeploymentStrategy:
    """
    Worker-pool deployment strategy.

    Guarantees:
    - Single-replica enforcement (multi-replica routing is the caller's job)
    - Atomic allocation via scheduler_repo
    - Rollback of allocation on worker_controller failure
    """

    def __init__(self, *, scheduler_repo, worker_controller):
        self.scheduler = scheduler_repo
        self.controller = worker_controller

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
        if replicas != 1:
            raise ValueError(
                "WorkerDeploymentStrategy supports only single-replica "
                "deployments; multi-replica is routed by the caller."
            )

        allocation_id = uuid.uuid4()
        allocated = False

        try:
            # Atomic allocate on the chosen node.
            ok, reason, _ = await self.scheduler.allocate(
                allocation_id=allocation_id,
                node_id=node_id,
                gpu=gpu_per_replica,
                vcpu=vcpu_per_replica,
                ram_gb=ram_gb_per_replica,
                priority=1000,
                owner_type="deployment",
                owner_id=str(deployment_id),
            )
            if not ok:
                raise RuntimeError(f"worker allocate failed: {reason}")
            allocated = True

            # Build the worker LoadModel spec.
            spec = {
                "deployment_id": str(deployment_id),
                "recipe": model.get("backend", "vllm"),
                "model": {
                    "artifact_uri": model["artifact_uri"],
                    "format": model.get("format", "hf"),
                    "backend": model.get("backend", "vllm"),
                },
                "config": model.get("config") or {},
                "gpu_indices": list(range(gpu_per_replica)),
                "port": 0,  # let the worker allocate
            }
            result = await self.controller.load_model(
                node_id=str(node_id), spec=spec,
            )
            if result.status != "ok":
                raise RuntimeError(
                    f"worker load_model failed: {result.detail}"
                )

            return {
                "runtime": "worker",
                "allocation_id": str(allocation_id),
                "allocation_ids": [str(allocation_id)],
                "node_ids": [str(node_id)],
                "endpoint": result.endpoint_url,
            }
        except Exception:
            if allocated:
                try:
                    await self.scheduler.release(allocation_id=allocation_id)
                except Exception:
                    pass
            raise


__all__ = ["WorkerDeploymentStrategy"]

"""
Direct-managed GPU worker deployment strategy.

Used when the target compute_inventory row has agent_kind='worker'. Places the
deployment via the placement engine, reserves resources via the scheduler,
then delegates the actual container launch to the connected worker over the
WS control channel.

Replaces the now-removed LLMdDeploymentStrategy + llmd_runtime path.
"""

from __future__ import annotations


class WorkerDeploymentStrategy:
    def __init__(
        self,
        placement_engine,
        scheduler_engine,
        worker_controller,
    ):
        self.placement = placement_engine
        self.scheduler = scheduler_engine
        self.controller = worker_controller

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
        # Place each replica on a worker-kind node.
        node_ids = []
        for _ in range(replicas):
            placement = await self.placement.place_workload(
                pool_id=pool_id,
                gpu_required=gpu_per_replica,
                vcpu_required=2,
                ram_gb_required=8,
                workload_type=workload_type,
            )
            node_ids.append(placement.node_id)

        allocations = []
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

        # MVP: one replica per worker. Multi-replica fan-out is a follow-up.
        # We pick the first node for the inference endpoint; subsequent
        # replicas are handled by an external traffic router that already
        # knows how to fan inference traffic across multiple endpoints.
        endpoints = []
        for node_id in node_ids:
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
            result = await self.controller.load_model(node_id=str(node_id), spec=spec)
            if result.status != "ok":
                raise RuntimeError(
                    f"worker load_model failed on {node_id}: {result.detail}"
                )
            endpoints.append(result.endpoint_url)

        return {
            "runtime": "worker",
            "allocations": allocations,
            "endpoint": endpoints[0],
            "endpoints": endpoints,
        }


__all__ = ["WorkerDeploymentStrategy"]

class DeploymentStrategy:
    async def deploy(self, *, deployment_id, model, pool_id,
                     replicas, gpu_per_replica, workload_type):
        raise NotImplementedError

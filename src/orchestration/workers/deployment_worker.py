class ModelDeploymentWorker:
    def __init__(self, repos, services):
        self.deployments = repos.deployments
        self.scheduler = services.scheduler
        self.placement = services.placement
        self.adapter = services.adapter
        self.runtime_resolver = services.runtime_resolver

    async def handle(self, event):
        deployment = await self.deployments.get(event["deployment_id"])

        # State machine
        if deployment.state == "PENDING":
            await self._schedule(deployment)

    async def _schedule(self, deployment):
        runtime = self.runtime_resolver.resolve(...)
        strategy = self._load_strategy(runtime)
        await strategy.deploy(...)

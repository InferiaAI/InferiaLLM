from uuid import UUID
from inferia.services.orchestration.services.llmd_runtime.health import wait_until_ready
from inferia.services.orchestration.services.llmd_runtime.spec import build_spec


class LLMdRuntime:
    def __init__(
        self,
        *,
        deployment_repo,
        llmd_client,
    ):
        self.deployments = deployment_repo
        self.client = llmd_client

    async def deploy(
        self,
        *,
        deployment_id: UUID,
        model,
        replicas: int,
        gpu_per_replica: int,
        node_names: list[str],
    ):
        spec = build_spec(
            deployment_id=str(deployment_id),
            model=model,
            replicas=replicas,
            gpu_per_replica=gpu_per_replica,
            node_names=node_names,
        )

        await self.client.apply(spec)

        try:
            await wait_until_ready(
                client=self.client,
                resource_name=spec["metadata"]["name"],
            )
        except Exception:
            # mark failed; reconciliation loop may retry later
            await self.deployments.update_state(
                deployment_id, "FAILED"
            )
            raise

        await self.deployments.attach_runtime(
            deployment_id=deployment_id,
            runtime="llmd",
            llmd_resource_name=spec["metadata"]["name"],
        )

        await self.deployments.update_state(
            deployment_id, "RUNNING"
        )

        return {
            "runtime": "llmd",
            "llmd_resource": spec["metadata"]["name"],
        }

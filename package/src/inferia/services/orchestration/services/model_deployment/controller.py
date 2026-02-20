from uuid import UUID, uuid4
from typing import Optional, List
import logging

logger = logging.getLogger(__name__)


class ModelDeploymentController:
    """
    Authoritative controller for model deployments.

    This class MUST match the gRPC service contract.
    No orchestration logic lives here — only intent + state.
    """

    def __init__(
        self,
        *,
        model_registry_repo,
        deployment_repo,
        outbox_repo,
        event_bus,
        pool_repo=None,
    ):
        self.models = model_registry_repo
        self.deployments = deployment_repo
        self.outbox = outbox_repo
        self.event_bus = event_bus
        self.pool_repo = pool_repo

    def _inject_workload_type(
        self, configuration: Optional[str], workload_type: str
    ) -> str:
        import json

        config_dict = {}
        if configuration:
            try:
                config_dict = json.loads(configuration)
            except json.JSONDecodeError:
                # Invalid JSON, start with empty config
                pass

        config_dict["workload_type"] = workload_type
        return json.dumps(config_dict)

    def _extract_workload_type(self, deployment: dict) -> str:
        """
        Resolve workload_type from persisted configuration.
        Defaults to inference for backward compatibility with older rows.
        """
        import json

        config = deployment.get("configuration")
        if isinstance(config, dict):
            workload_type = config.get("workload_type")
            return str(workload_type) if workload_type else "inference"

        if isinstance(config, str):
            try:
                parsed = json.loads(config)
                workload_type = parsed.get("workload_type")
                return str(workload_type) if workload_type else "inference"
            except json.JSONDecodeError:
                return "inference"

        return "inference"

    # -------------------------------------------------
    # CREATE / DEPLOY
    # -------------------------------------------------
    async def deploy_model(
        self,
        *,
        model_name: str,
        model_version: str,
        pool_id: UUID,
        replicas: int,
        gpu_per_replica: int,
        workload_type: str,
        # Unified Deployment Fields
        engine: Optional[str] = None,
        configuration: Optional[str] = None,
        owner_id: Optional[str] = None,
        endpoint: Optional[str] = None,
        org_id: Optional[str] = None,
        policies: Optional[str] = None,
        inference_model: Optional[str] = None,
        model_type: Optional[str] = "inference",
    ) -> UUID:
        model_id = None

        if self.pool_repo:
            pool = await self.pool_repo.get(pool_id)
            if not pool:
                raise ValueError(f"Pool {pool_id} not found")
            if not pool.get("is_active", True):
                raise ValueError(f"Pool {pool_id} is not active")

        # If engine is NOT provided, assume legacy flow via Model Registry
        if not engine:
            # Validate model or auto-register
            model = await self.models.get_model(model_name, model_version)
            if not model:
                # Auto-register logic for smoother UX in this phase
                logger.info(
                    f"Model {model_name}:{model_version} not found, auto-registering..."
                )
                model_id_val = await self.models.register_model(
                    name=model_name,
                    version=model_version,
                    backend="vllm",  # Defaulting to vllm for now
                    artifact_uri=model_name,  # Defaulting artifact_uri to model name (e.g. HF ID)
                    config={},
                )
                model_id = model_id_val
            else:
                model_id = model["model_id"]

        # If engine IS provided, we treat model_name as the artifact URI / model identifier directly
        # and skip strict registry requirement for now (or we could still register it).
        # For this task, we make model_id optional in DB, so we can proceed without it if engine is set.

        deployment_id = uuid4()

        # External deployments (API passthrough) don't need worker processing
        is_external = workload_type == "external"
        initial_state = "RUNNING" if is_external else "PENDING"

        # --- transactional intent creation ---
        async with self.deployments.transaction() as tx:
            await self.deployments.create(
                deployment_id=deployment_id,
                model_id=model_id,
                pool_id=pool_id,
                replicas=replicas,
                gpu_per_replica=gpu_per_replica,
                state=initial_state,
                # Unified fields
                engine=engine,
                configuration=self._inject_workload_type(configuration, workload_type),
                endpoint=endpoint,
                model_name=model_name,  # Explicitly store name
                owner_id=owner_id,
                org_id=org_id,
                policies=policies,
                inference_model=inference_model,
                model_type=model_type,
                tx=tx,
            )

            # --- transactional outbox ---
            await self.outbox.enqueue(
                aggregate_type="model_deployment",
                aggregate_id=deployment_id,
                event_type="model.deployment.requested",
                payload={
                    "deployment_id": str(deployment_id),
                    "pool_id": str(pool_id),
                    "replicas": replicas,
                    "gpu_per_replica": gpu_per_replica,
                    "workload_type": workload_type,
                    "engine": engine,
                    "configuration": configuration,
                    "owner_id": owner_id,
                    "model_name": model_name,
                    "model_type": model_type,
                },
                tx=tx,
            )

            # Only emit deploy event for compute deployments (worker-managed)
            # External deployments are already RUNNING and don't need worker processing
            if not is_external:
                await self.event_bus.publish(
                    "model.deploy.requested",
                    {
                        "deployment_id": str(deployment_id),
                        "model_id": str(model_id) if model_id else None,
                        "pool_id": str(pool_id),
                        "replicas": replicas,
                        "gpu_per_replica": gpu_per_replica,
                        "workload_type": workload_type,
                        "engine": engine,
                        "configuration": configuration,
                        "owner_id": owner_id,
                        "model_type": model_type,
                    },
                )

        return deployment_id

    # -------------------------------------------------
    # READ
    # -------------------------------------------------
    async def get_deployment(
        self,
        deployment_id: UUID,
    ) -> Optional[dict]:
        return await self.deployments.get(deployment_id)

    async def list_deployments(
        self,
        pool_id: Optional[UUID] = None,
        org_id: Optional[str] = None,
    ) -> List[dict]:
        return await self.deployments.list(pool_id=pool_id, org_id=org_id)

    # -------------------------------------------------
    # DELETE
    # -------------------------------------------------
    async def request_delete(
        self,
        deployment_id: UUID,
    ) -> None:
        d = await self.deployments.get(deployment_id)
        if not d:
            raise ValueError("Deployment not found")

        if d["state"] in ("TERMINATED", "TERMINATING"):
            return

        async with self.deployments.transaction() as tx:
            await self.deployments.update_state(deployment_id, "TERMINATING", tx=tx)

            await self.outbox.enqueue(
                aggregate_type="model_deployment",
                aggregate_id=deployment_id,
                event_type="model.deployment.terminate",
                payload={
                    "deployment_id": str(deployment_id),
                },
                tx=tx,
            )

        await self.event_bus.publish(
            "model.terminate.requested",
            {
                "deployment_id": str(deployment_id),
            },
        )

    # -------------------------------------------------
    # START (Redeploy)
    # -------------------------------------------------
    async def start_deployment(
        self,
        deployment_id: UUID,
    ) -> str:
        d = await self.deployments.get(deployment_id)
        if not d:
            raise ValueError("Deployment not found")

        if d["state"] not in ("STOPPED", "TERMINATED", "FAILED"):
            raise ValueError(f"Cannot start deployment in state {d['state']}")

        workload_type = self._extract_workload_type(d)

        # External/provider deployments don't require compute orchestration.
        # Move directly to RUNNING and skip worker deployment events.
        if workload_type == "external":
            await self.deployments.update_state(deployment_id, "RUNNING")
            return "RUNNING"

        # Reset to PENDING so worker picks it up
        await self.deployments.update_state(deployment_id, "PENDING")

        # Emit deploy requested event again
        await self.event_bus.publish(
            "model.deploy.requested",
            {
                "deployment_id": str(deployment_id),
                "model_id": str(d["model_id"]) if d["model_id"] else None,
                "pool_id": str(d["pool_id"]),
                "replicas": d["replicas"],
                "gpu_per_replica": d["gpu_per_replica"],
                "workload_type": workload_type,
                "engine": d.get("engine"),
                "configuration": d.get("configuration"),  # Should encompass json
                "owner_id": d.get("owner_id"),
            },
        )

        return "PENDING"

    async def update_deployment(
        self,
        *,
        deployment_id: UUID,
        configuration: Optional[str] = None,
        inference_model: Optional[str] = None,
        endpoint: Optional[str] = None,
        replicas: Optional[int] = None,
    ) -> None:
        """
        Update an existing deployment.
        """
        d = await self.deployments.get(deployment_id)
        if not d:
            raise ValueError("Deployment not found")

        await self.deployments.update(
            deployment_id=deployment_id,
            configuration=configuration,
            inference_model=inference_model,
            endpoint=endpoint,
            replicas=replicas,
        )

        # Emit event for tracking
        await self.event_bus.publish(
            "model.deployment.updated",
            {
                "deployment_id": str(deployment_id),
                "configuration": configuration,
                "inference_model": inference_model,
                "endpoint": endpoint,
                "replicas": replicas,
            },
        )

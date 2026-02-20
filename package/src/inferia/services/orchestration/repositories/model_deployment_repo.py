from uuid import UUID
from typing import List, Optional
from inferia.services.orchestration.repositories.base_repo import BaseRepository


class ModelDeploymentRepository(BaseRepository):
    def __init__(self, db, event_bus):
        super().__init__(db)
        self.event_bus = event_bus

    async def create(
        self,
        *,
        deployment_id: UUID,
        model_id: Optional[UUID],  # Made optional
        pool_id: UUID,
        replicas: int,
        gpu_per_replica: int,
        state: str,
        # Unified Deployment Fields
        engine: Optional[str] = None,
        configuration: Optional[str] = None,  # JSON string or dict? DB is jsonb.
        endpoint: Optional[str] = None,
        model_name: Optional[str] = None,
        owner_id: Optional[str] = None,
        org_id: Optional[str] = None,
        policies: Optional[str] = None,
        inference_model: Optional[str] = None,
        model_type: Optional[str] = "inference",
        tx=None,
    ):
        q = """
        INSERT INTO model_deployments (
            deployment_id,
            model_id,
            pool_id,
            replicas,
            gpu_per_replica,
            state,
            engine,
            configuration,
            endpoint,
            model_name,
            owner_id,
            org_id,
            policies,
            inference_model,
            model_type
        )
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15)
        """
        # Ensure configuration is passed as json
        import json

        if configuration and isinstance(configuration, dict):
            configuration = json.dumps(configuration)

        if policies and isinstance(policies, str):
            # Ensure policies is valid json if string
            try:
                json.loads(policies)
            except json.JSONDecodeError:
                policies = "{}"

        if tx:
            await tx.execute(
                q,
                deployment_id,
                model_id,
                pool_id,
                replicas,
                gpu_per_replica,
                state,
                engine,
                configuration,
                endpoint,
                model_name,
                owner_id,
                org_id,
                policies,
                inference_model,
                model_type,
            )
        else:
            async with self.db.acquire() as c:
                await c.execute(
                    q,
                    deployment_id,
                    model_id,
                    pool_id,
                    replicas,
                    gpu_per_replica,
                    state,
                    engine,
                    configuration,
                    endpoint,
                    model_name,
                    owner_id,
                    org_id,
                    policies,
                    inference_model,
                    model_type,
                )

        # await self.event_bus.publish(
        #     "model.deploy.requested",
        #     {
        #         "deployment_id": str(deployment_id),
        #         "model_id": str(model_id) if model_id else None,
        #         "pool_id": str(pool_id),
        #         "replicas": replicas,
        #         "gpu_per_replica": gpu_per_replica,
        #         "state": state,
        #         "engine": engine,
        #         "owner_id": owner_id,
        #     },
        # )

    async def update_state(self, deployment_id: UUID, state: str, tx=None):
        q = """
        UPDATE model_deployments
        SET state=$2, updated_at=now()
        WHERE deployment_id=$1
        """
        if tx:
            await tx.execute(q, deployment_id, state)
        else:
            async with self.db.acquire() as c:
                await c.execute(q, deployment_id, state)

        await self.event_bus.publish(
            "deployment.state_changed",
            {
                "deployment_id": str(deployment_id),
                "state": state,
            },
        )

    async def update_state_if(
        self,
        deployment_id: UUID,
        expected_state: str,
        new_state: str,
        tx=None,
    ) -> bool:
        """
        Atomically update state only if_state.
        Returns current state matches expected True if update was successful, False otherwise.
        """
        q = """
        UPDATE model_deployments
        SET state=$3, updated_at=now()
        WHERE deployment_id=$1 AND state=$2
        """
        if tx:
            result = await tx.execute(q, deployment_id, expected_state, new_state)
        else:
            async with self.db.acquire() as c:
                result = await c.execute(q, deployment_id, expected_state, new_state)

        updated = result != "UPDATE 0"

        if updated:
            await self.event_bus.publish(
                "deployment.state_changed",
                {
                    "deployment_id": str(deployment_id),
                    "state": new_state,
                },
            )

        return updated

    async def attach_runtime(
        self,
        *,
        deployment_id: UUID,
        allocation_ids: Optional[List[UUID]] = None,
        node_ids: Optional[List[UUID]] = None,
        llmd_resource_name: Optional[str] = None,
        runtime: Optional[str] = None,
    ):
        q = """
        UPDATE model_deployments
        SET
            allocation_ids=$2,
            node_ids=$3,
            llmd_resource_name=$4,
            updated_at=now()
        WHERE deployment_id=$1
        """
        async with self.db.acquire() as c:
            await c.execute(
                q,
                deployment_id,
                allocation_ids,
                node_ids,
                llmd_resource_name,
            )

        await self.event_bus.publish(
            "deployment.runtime_attached",
            {
                "deployment_id": str(deployment_id),
                "allocation_ids": (
                    [str(a) for a in allocation_ids] if allocation_ids else None
                ),
                "node_ids": ([str(n) for n in node_ids] if node_ids else None),
                "llmd_resource_name": llmd_resource_name,
            },
        )

    async def update_endpoint(
        self,
        deployment_id: UUID,
        endpoint: str,
        model_name: Optional[str] = None,
    ):
        q = """
        UPDATE model_deployments
        SET endpoint=$2, model_name=COALESCE($3, model_name), updated_at=now()
        WHERE deployment_id=$1
        """
        async with self.db.acquire() as c:
            await c.execute(q, deployment_id, endpoint, model_name)

        await self.event_bus.publish(
            "deployment.endpoint_updated",
            {
                "deployment_id": str(deployment_id),
                "endpoint": endpoint,
                "model_name": model_name,
            },
        )

    async def update(
        self,
        deployment_id: UUID,
        *,
        configuration: Optional[str] = None,
        inference_model: Optional[str] = None,
        endpoint: Optional[str] = None,
        replicas: Optional[int] = None,
        tx=None,
    ):
        fields = []
        args = [deployment_id]
        idx = 2

        if configuration is not None:
            if isinstance(configuration, dict):
                import json
                configuration = json.dumps(configuration)
            fields.append(f"configuration=${idx}")
            args.append(configuration)
            idx += 1

        if inference_model is not None:
            fields.append(f"inference_model=${idx}")
            args.append(inference_model)
            idx += 1

        if endpoint is not None:
            fields.append(f"endpoint=${idx}")
            args.append(endpoint)
            idx += 1

        if replicas is not None:
            fields.append(f"replicas=${idx}")
            args.append(replicas)
            idx += 1

        if not fields:
            return

        q = f"""
        UPDATE model_deployments
        SET {', '.join(fields)}, updated_at=now()
        WHERE deployment_id=$1
        """

        if tx:
            await tx.execute(q, *args)
        else:
            async with self.db.acquire() as c:
                await c.execute(q, *args)

        await self.event_bus.publish(
            "deployment.updated",
            {
                "deployment_id": str(deployment_id),
                "fields": fields,
            },
        )

    async def get(self, deployment_id: UUID):
        q = "SELECT * FROM model_deployments WHERE deployment_id=$1"
        async with self.db.acquire() as c:
            row = await c.fetchrow(q, deployment_id)
            return dict(row) if row else None

    async def list(self, pool_id: Optional[UUID] = None, org_id: Optional[str] = None):
        conditions = []
        args = []
        idx = 1

        if pool_id:
            conditions.append(f"pool_id=${idx}")
            args.append(pool_id)
            idx += 1

        if org_id:
            conditions.append(f"org_id=${idx}")
            args.append(org_id)
            idx += 1

        where_clause = "WHERE " + " AND ".join(conditions) if conditions else ""

        q = f"""
        SELECT * FROM model_deployments
        {where_clause}
        ORDER BY created_at DESC
        """

        async with self.db.acquire() as c:
            rows = await c.fetch(q, *args)
            return [dict(r) for r in rows]

    async def list_by_state(self, state: str):
        q = """
        SELECT * FROM model_deployments
        WHERE state=$1
        ORDER BY created_at DESC
        """
        async with self.db.acquire() as c:
            rows = await c.fetch(q, state)
            return [dict(r) for r in rows]

    async def delete(self, deployment_id: UUID):
        """Permanently delete a deployment from the database."""
        q = """
        DELETE FROM model_deployments
        WHERE deployment_id=$1
        """
        async with self.db.acquire() as c:
            await c.execute(q, deployment_id)

        await self.event_bus.publish(
            "deployment.deleted",
            {
                "deployment_id": str(deployment_id),
            },
        )

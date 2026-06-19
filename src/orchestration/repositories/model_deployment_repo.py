from __future__ import annotations

from datetime import datetime
from uuid import UUID
from typing import List, Optional
from orchestration.repositories.base_repo import BaseRepository


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
        target_pool_id: Optional[UUID] = None,
        target_node_id: Optional[UUID] = None,
        auto_replica_enabled: bool = False,
        tokens_per_second_threshold: Optional[float] = None,
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
            model_type,
            target_pool_id,
            target_node_id,
            auto_replica_enabled,
            tokens_per_second_threshold
        )
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19)
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
                target_pool_id,
                target_node_id,
                auto_replica_enabled,
                tokens_per_second_threshold,
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
                    target_pool_id,
                    target_node_id,
                    auto_replica_enabled,
                    tokens_per_second_threshold,
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

    async def update_state(
        self,
        deployment_id: UUID,
        state: str,
        tx=None,
        error_message: str | None = None,
    ):
        # Clear error_message when transitioning to non-failure states
        if state not in ("FAILED", "RETRYING"):
            error_message = None

        q = """
        UPDATE model_deployments
        SET state=$2, error_message=$3, updated_at=now()
        WHERE deployment_id=$1
        """
        if tx:
            await tx.execute(q, deployment_id, state, error_message)
        else:
            async with self.db.acquire() as c:
                await c.execute(q, deployment_id, state, error_message)

        if self.event_bus:
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
        error_message: str | None = None,
    ) -> bool:
        """
        Atomically update state only if current state matches expected.
        Returns True if update was successful, False otherwise.
        """
        # Clear error_message when transitioning to non-failure states
        if new_state not in ("FAILED", "RETRYING"):
            error_message = None

        q = """
        UPDATE model_deployments
        SET state=$3, error_message=$4, updated_at=now()
        WHERE deployment_id=$1 AND state=$2
        """
        if tx:
            result = await tx.execute(
                q, deployment_id, expected_state, new_state, error_message
            )
        else:
            async with self.db.acquire() as c:
                result = await c.execute(
                    q, deployment_id, expected_state, new_state, error_message
                )

        updated = result != "UPDATE 0"

        if updated and self.event_bus:
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

    async def list(
        self,
        pool_id: Optional[UUID] = None,
        org_id: Optional[str] = None,
        *,
        limit: int = 100,
        offset: int = 0,
    ):
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
        LIMIT ${idx} OFFSET ${idx + 1}
        """
        args.extend([limit, offset])

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

    async def update_auto_replica(
        self,
        deployment_id: UUID,
        *,
        auto_replica_enabled: bool | None = None,
        tokens_per_second_threshold: float | None = None,
        last_scale_at: datetime | None = None,
    ):
        updates = []
        args = [deployment_id]
        idx = 2
        if auto_replica_enabled is not None:
            updates.append(f"auto_replica_enabled=${idx}")
            args.append(auto_replica_enabled)
            idx += 1
        if tokens_per_second_threshold is not None:
            updates.append(f"tokens_per_second_threshold=${idx}")
            args.append(tokens_per_second_threshold)
            idx += 1
        if last_scale_at is not None:
            updates.append(f"auto_replica_last_scale_at=${idx}")
            args.append(last_scale_at)
            idx += 1
        if not updates:
            return
        q = f"""
        UPDATE model_deployments
        SET {', '.join(updates)}, updated_at=now()
        WHERE deployment_id=$1
        """
        async with self.db.acquire() as c:
            await c.execute(q, *args)

    async def list_auto_replica_deployments(self) -> list[dict]:
        """List RUNNING deployments with auto_replica_enabled=true."""
        q = """
        SELECT * FROM model_deployments
        WHERE state IN ('RUNNING', 'DEPLOYING')
          AND auto_replica_enabled = true
        ORDER BY created_at DESC
        """
        async with self.db.acquire() as c:
            rows = await c.fetch(q)
            return [dict(r) for r in rows]

    async def update_configuration(self, deployment_id: UUID, configuration: dict):
        """Update the configuration JSONB field for a deployment."""
        import json

        q = """
        UPDATE model_deployments
        SET configuration=$2, updated_at=now()
        WHERE deployment_id=$1
        """
        config_json = json.dumps(configuration)
        async with self.db.acquire() as c:
            await c.execute(q, deployment_id, config_json)

    async def list_pending_for_pool(
        self,
        pool_id: UUID,
        *,
        tx=None,
    ) -> list[dict]:
        """All deployments waiting for a node in this pool, ordered FIFO
        by created_at.

        When `tx` is provided, the FOR UPDATE SKIP LOCKED row locks live
        for the lifetime of that transaction — concurrent linker runs
        will split the work without overlapping. When `tx` is None, the
        locks are released the instant the SELECT completes (asyncpg
        autocommits on connection release) and the lock hint is a no-op
        — only safe for non-concurrent callers.

        The SELECT aliases deployment_id AS id so callers can read
        `row["id"]` — DeploymentLinker uses this naming.
        """
        q = """
            SELECT deployment_id AS id, target_node_id, gpu_per_replica,
                   replicas, model_name, inference_model, engine, configuration
              FROM model_deployments
             WHERE target_pool_id = $1
               AND state = 'PENDING_NODE'
          ORDER BY created_at ASC
               FOR UPDATE SKIP LOCKED
            """
        if tx is not None:
            rows = await tx.fetch(q, pool_id)
        else:
            async with self.db.acquire() as conn:
                rows = await conn.fetch(q, pool_id)
        return [dict(r) for r in rows]

    async def list_deploying_for_node(self, node_id, *, tx=None):
        """DEPLOYING deploys bound to a node — used by the linker to RE-DRIVE
        load_model for deploys orphaned when the control plane restarted with an
        in-flight load_model (the worker-channel ``on_worker_ready`` hook only
        binds PENDING_NODE deploys, so an already-DEPLOYING deploy is otherwise
        never re-driven and stays stuck DEPLOYING with no container). Same
        column projection as ``list_pending_for_pool`` so ``_spec_from_pending``
        works unchanged. No row lock — this is a recovery read, not a claim."""
        q = """
            SELECT deployment_id AS id, target_node_id, gpu_per_replica,
                   replicas, model_name, inference_model, engine, configuration
              FROM model_deployments
             WHERE target_node_id = $1
               AND state = 'DEPLOYING'
          ORDER BY created_at ASC
            """
        if tx is not None:
            rows = await tx.fetch(q, node_id)
        else:
            async with self.db.acquire() as conn:
                rows = await conn.fetch(q, node_id)
        return [dict(r) for r in rows]

    async def bind_to_node(
        self,
        deployment_id: UUID,
        node_id: UUID,
        *,
        tx=None,
    ) -> None:
        q = ("UPDATE model_deployments SET target_node_id = $2 "
             "WHERE deployment_id = $1")
        if tx is not None:
            await tx.execute(q, deployment_id, node_id)
        else:
            async with self.db.acquire() as conn:
                await conn.execute(q, deployment_id, node_id)

    async def set_state(
        self,
        deployment_id: UUID,
        state: str,
        *,
        tx=None,
    ) -> None:
        """Placement-internal state transition.

        Does NOT publish on event_bus — that's intentional. The
        DeploymentLinker calls this for the PENDING_NODE → DEPLOYING
        transition and any subscribers downstream that care about
        observable state changes should subscribe to the event_bus
        topic published by update_state instead.
        """
        q = ("UPDATE model_deployments SET state = $2 "
             "WHERE deployment_id = $1")
        if tx is not None:
            await tx.execute(q, deployment_id, state)
        else:
            async with self.db.acquire() as conn:
                await conn.execute(q, deployment_id, state)

    async def unbind(
        self,
        deployment_id: UUID,
        *,
        tx=None,
    ) -> None:
        q = ("UPDATE model_deployments SET target_node_id = NULL "
             "WHERE deployment_id = $1")
        if tx is not None:
            await tx.execute(q, deployment_id)
        else:
            async with self.db.acquire() as conn:
                await conn.execute(q, deployment_id)

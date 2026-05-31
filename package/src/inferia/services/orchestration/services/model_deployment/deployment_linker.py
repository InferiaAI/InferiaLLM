"""DeploymentLinker: bind PENDING_NODE deploys to a freshly-ready node.

Called from two sites:
  - api/workers.register_worker, immediately after a worker self-registers.
  - The provisioning reconciler's BootstrapHandler success path, when a
    Pulumi-provisioned EC2 boots its worker and registers.

Both paths flow through worker registration ultimately, so a single hook
covers both.

Concurrency: the bind loop runs inside ONE transaction so list_pending_for_pool's
FOR UPDATE SKIP LOCKED actually holds locks while allocate_gpu / bind_to_node /
set_state run. Two linker runs racing for the same pool (e.g. two workers
registering simultaneously) get disjoint slices of the pending deploys.

load_model is invoked AFTER the transaction commits because it's a
worker-control-channel call that can take seconds and must not block the
transaction. If a load_model fails, we release_gpu and mark FAILED on the
deploy.
"""
from __future__ import annotations

import logging
from uuid import UUID

import asyncpg

from inferia.services.orchestration.repositories.inventory_repo import (
    InventoryRepository,
)
from inferia.services.orchestration.repositories.model_deployment_repo import (
    ModelDeploymentRepository,
)

logger = logging.getLogger(__name__)


def _spec_from_pending(deploy: dict, gpu_required: int) -> dict:
    """Build the load_model spec from a list_pending_for_pool row.

    asyncpg returns ``configuration`` as a JSON string. Parse it if so.
    """
    import json as _json

    cfg = deploy.get("configuration") or {}
    if isinstance(cfg, str):
        try:
            cfg = _json.loads(cfg)
        except (ValueError, TypeError):
            cfg = {}
    if not isinstance(cfg, dict):
        cfg = {}
    model_block = cfg.get("model") or {}
    artifact_uri = (
        model_block.get("artifact_uri")
        or cfg.get("artifact_uri")
        or deploy.get("model_name")
        or ""
    )
    return {
        "deployment_id": str(deploy["id"]),
        "recipe": deploy.get("engine") or "vllm",
        "model": {
            "artifact_uri": str(artifact_uri),
            "format": str(model_block.get("format") or cfg.get("format") or "hf"),
            "backend": str(
                model_block.get("backend")
                or cfg.get("backend")
                or deploy.get("engine")
                or "vllm"
            ),
        },
        "config": cfg.get("config") or {},
        "gpu_indices": list(range(gpu_required)),
        "port": 0,
    }


class DeploymentLinker:
    def __init__(
        self,
        *,
        db_pool: asyncpg.Pool,
        inventory_repo: InventoryRepository,
        deployment_repo: ModelDeploymentRepository,
        worker_controller,
    ) -> None:
        self._db = db_pool
        self._inventory = inventory_repo
        self._deploys = deployment_repo
        self._controller = worker_controller

    async def on_worker_ready(self, node_id: UUID) -> None:
        async with self._db.acquire() as conn:
            pool_id_row = await conn.fetchrow(
                "SELECT pool_id FROM compute_inventory WHERE id=$1",
                node_id,
            )
            if pool_id_row is None:
                logger.warning("on_worker_ready: node %s not found", node_id)
                return
            pool_id = pool_id_row["pool_id"]

            bound: list[dict] = []
            async with conn.transaction():
                pending = await self._deploys.list_pending_for_pool(
                    pool_id, tx=conn,
                )
                for deploy in pending:
                    gpu_required = int(deploy.get("gpu_per_replica") or 1)
                    tgt = deploy.get("target_node_id")
                    if tgt == node_id:
                        # Already bound to THIS node with its GPU allocated at
                        # ColdStart/CoWait deploy time (create_placeholder
                        # initial_alloc). Re-allocating would double-count and
                        # fail on a now-full node — just promote to DEPLOYING.
                        await self._deploys.set_state(
                            deploy["id"], "DEPLOYING", tx=conn,
                        )
                        bound.append(deploy)
                    elif tgt is None:
                        # Unbound (e.g. a worker-pool deploy waiting for a
                        # self-registered worker): allocate + bind now.
                        ok = await self._inventory.allocate_gpu(
                            node_id, gpu_required, tx=conn,
                        )
                        if not ok:
                            # node full; remaining unbound deploys stay PENDING
                            break
                        await self._deploys.bind_to_node(
                            deploy["id"], node_id, tx=conn,
                        )
                        await self._deploys.set_state(
                            deploy["id"], "DEPLOYING", tx=conn,
                        )
                        bound.append(deploy)
                    # else: bound to a DIFFERENT in-flight node — leave it for
                    # that node's on_worker_ready.

        # Transaction committed. Fire load_model OUTSIDE the transaction.
        for deploy in bound:
            gpu_required = int(deploy.get("gpu_per_replica") or 1)
            try:
                spec = _spec_from_pending(deploy, gpu_required)
                await self._controller.load_model(
                    node_id=str(node_id), spec=spec,
                )
            except Exception as e:
                logger.exception(
                    "linker: load_model failed for deploy=%s: %s",
                    deploy["id"], e,
                )
                # Atomic rollback: release GPU + mark FAILED in one txn so a
                # partial failure can't leave gpu_allocated out of sync with
                # deploy state.
                async with self._db.acquire() as conn:
                    async with conn.transaction():
                        await self._inventory.release_gpu(
                            node_id, gpu_required, tx=conn,
                        )
                        await self._deploys.set_state(
                            deploy["id"], "FAILED", tx=conn,
                        )

        if bound:
            logger.info(
                "linker: bound %d pending deploys to node=%s pool=%s",
                len(bound), node_id, pool_id,
            )

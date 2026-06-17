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

from orchestration.repositories.inventory_repo import (
    InventoryRepository,
)
from orchestration.repositories.model_deployment_repo import (
    ModelDeploymentRepository,
)
from orchestration.models.model_deployment.model_ref import (
    resolve_artifact_uri,
)
from orchestration.models.model_deployment.mirror_decision import (
    resolve_and_apply_mirror,
)
from orchestration.models.model_cache import deps as _mc_deps

logger = logging.getLogger(__name__)


DISAGG_RECIPE_MAP = {
    "vllm": "vllm-prefill-decode",
    "sglang": "sglang-prefill-decode",
}


def _resolve_recipe(engine: str, prefill_replicas: int, decode_replicas: int) -> str:
    """Upgrade recipe to prefill-decode variant when disagg is active."""
    if (prefill_replicas > 0 or decode_replicas > 0) and engine not in (
        "vllm-prefill-decode", "sglang-prefill-decode",
    ):
        return DISAGG_RECIPE_MAP.get(engine, engine)
    return engine


def _build_disagg_spec(spec: dict, cfg: dict, prefill_replicas: int, decode_replicas: int, gpu_indices: list[int]) -> None:
    """Mutate spec in-place with disagg fields."""
    spec["prefill_replicas"] = prefill_replicas
    spec["decode_replicas"] = decode_replicas or 1
    # GPU index assignment is owned by the worker's GPUAllocator.
    # Omit prefill_gpu_indices / decode_gpu_indices so the worker
    # defaults both to GPUIndices and partitions via its allocator.


def _spec_from_pending(deploy: dict, gpu_required: int) -> dict:
    """Build the load_model spec from a list_pending_for_pool row.

    asyncpg returns ``configuration`` as a JSON string. Parse it if so.
    When the config contains ``prefill_replicas`` / ``decode_replicas``
    (set by the dashboard's GPU slider) the recipe is upgraded to the
    ``-prefill-decode`` variant so the worker dispatcher takes the
    multi-container path.
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
    model_block = cfg.get("model")
    if not isinstance(model_block, dict):
        model_block = {}
    artifact_uri = resolve_artifact_uri(
        configuration=cfg,
        inference_model=deploy.get("inference_model"),
        model_name=deploy.get("model_name"),
    ) or ""
    recipe = deploy.get("engine") or "vllm"
    gpu_indices = list(range(gpu_required))
    prefill_replicas = cfg.get("prefill_replicas", 0)
    decode_replicas = cfg.get("decode_replicas", 0)
    recipe = _resolve_recipe(deploy.get("engine") or "vllm", prefill_replicas, decode_replicas)

    spec: dict = {
        "deployment_id": str(deploy["id"]),
        "recipe": recipe,
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
        "gpu_indices": gpu_indices,
        "port": 0,
        "env": dict(cfg.get("env") or {}),
    }

    if prefill_replicas > 0 or decode_replicas > 0:
        _build_disagg_spec(spec, cfg, prefill_replicas, decode_replicas, gpu_indices)

    return spec


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
                try:
                    from orchestration.config import settings as _s
                    _mirror_base = getattr(_s, "model_mirror_base", "") or ""
                except Exception:
                    _mirror_base = ""
                await resolve_and_apply_mirror(
                    spec, recipe=spec["recipe"],
                    artifact_uri=spec["model"]["artifact_uri"],
                    mirror_base=_mirror_base, cache_repo=_mc_deps.get("repo"),
                )
                result = await self._controller.load_model(
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
            else:
                # The worker can return a CommandResult with status='failed'
                # (e.g. readiness probe timed out, pull failed) WITHOUT raising
                # — controller.load_model returns the body verbatim. Treat that
                # as a load failure: release the GPU + mark FAILED, and DO NOT
                # publish an endpoint or report RUNNING (the model is not
                # serving). Previously this fell through to the success branch
                # and reported RUNNING for a model that never loaded.
                if getattr(result, "status", None) == "failed":
                    logger.error(
                        "linker: load_model returned status=failed for "
                        "deploy=%s: %s",
                        deploy["id"], getattr(result, "detail", ""),
                    )
                    async with self._db.acquire() as conn:
                        async with conn.transaction():
                            await self._inventory.release_gpu(
                                node_id, gpu_required, tx=conn,
                            )
                            await self._deploys.set_state(
                                deploy["id"], "FAILED", tx=conn,
                            )
                    continue

                # Model loaded on the worker. Promote DEPLOYING → RUNNING so
                # the dashboard reflects the live deployment. The warm-deploy
                # path does this (controller/worker set RUNNING); the
                # EC2-bootstrap path previously left the deploy stuck
                # DEPLOYING forever even though the model was serving.
                async with self._db.acquire() as conn:
                    await self._deploys.set_state(
                        deploy["id"], "RUNNING", tx=conn,
                    )
                # Publish the inference endpoint so the data plane can route
                # to this worker's :8080 inference proxy. We use the node's
                # CP-reachable advertise_url — NOT the worker's reported
                # endpoint_url, which is a 127.0.0.1:<port> loopback useless
                # to the control plane. The proxy auths with the pool
                # inference_token and routes by X-Inferia-Deployment-Id; the
                # inference data plane attaches both. Without this the
                # deployment has endpoint='' and the sandbox "never connects
                # to the node".
                try:
                    async with self._db.acquire() as conn:
                        advertise = await conn.fetchval(
                            "SELECT advertise_url FROM compute_inventory "
                            "WHERE id=$1",
                            node_id,
                        )
                    if advertise:
                        await self._deploys.update_endpoint(
                            deploy["id"], advertise,
                        )
                    else:
                        logger.warning(
                            "linker: node=%s has no advertise_url; deploy=%s "
                            "endpoint not set (inference unreachable)",
                            node_id, deploy["id"],
                        )
                except Exception:
                    logger.exception(
                        "linker: failed to set endpoint for deploy=%s",
                        deploy["id"],
                    )

        if bound:
            logger.info(
                "linker: bound %d pending deploys to node=%s pool=%s",
                len(bound), node_id, pool_id,
            )

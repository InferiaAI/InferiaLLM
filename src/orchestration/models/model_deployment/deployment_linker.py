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
    model_block = cfg.get("model")
    if not isinstance(model_block, dict):
        model_block = {}
    # The real model identifier for ollama lives in cfg["model_id"] (a bare
    # name:tag); resolve_artifact_uri reads it and guarantees a scheme the
    # worker accepts. Falling back to model_name (the display name) is the
    # bug this replaces — it shipped e.g. "hjg" instead of "gemma3:4b".
    artifact_uri = resolve_artifact_uri(
        configuration=cfg,
        inference_model=deploy.get("inference_model"),
        model_name=deploy.get("model_name"),
    ) or ""
    recipe = deploy.get("engine") or "vllm"
    gpu_indices = list(range(gpu_required))
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

        # Transaction committed. Drive load_model OUTSIDE the transaction for
        # the deploys we just bound.
        for deploy in bound:
            await self._drive_deploy(node_id, deploy)

        # RE-DRIVE orphaned DEPLOYING deploys. A deploy whose load_model was
        # in flight when the control plane restarted (or whose linker task
        # otherwise died) stays DEPLOYING forever: it is no longer PENDING_NODE
        # so the bind loop above skips it, /start reprovisions a NEW node, and
        # nothing else re-fires load_model — leaving the deployment "deploying"
        # with no container on the worker. On worker (re)connect the prior
        # control channel is dead, so re-firing load_model is safe + idempotent.
        # Skip the ones we just drove above.
        driven = {d["id"] for d in bound}
        try:
            orphaned = [
                d for d in await self._deploys.list_deploying_for_node(node_id)
                if d["id"] not in driven
            ]
        except Exception:
            logger.exception(
                "linker: failed to list orphaned DEPLOYING deploys for node=%s",
                node_id,
            )
            orphaned = []
        for deploy in orphaned:
            logger.info(
                "linker: re-driving orphaned DEPLOYING deploy=%s on node=%s "
                "(load_model never completed; control-plane restart?)",
                deploy["id"], node_id,
            )
            await self._drive_deploy(node_id, deploy)

        if bound or orphaned:
            logger.info(
                "linker: drove %d new + %d re-driven deploy(s) on node=%s pool=%s",
                len(bound), len(orphaned), node_id, pool_id,
            )

    async def _drive_deploy(self, node_id: UUID, deploy: dict) -> None:
        """Fire load_model for one already-bound deploy, then promote
        DEPLOYING → RUNNING (and publish the endpoint) on success, or release
        the GPU + mark FAILED on failure. The GPU was allocated at bind time, so
        this never re-allocates — on failure it RELEASES the existing
        allocation. Shared by the freshly-bound path and the orphan re-drive."""
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
                "linker: load_model failed for deploy=%s: %s", deploy["id"], e,
            )
            # Atomic rollback: release GPU + mark FAILED in one txn so a
            # partial failure can't leave gpu_allocated out of sync with state.
            # Record the reason via update_state (NOT set_state) so the failure
            # is VISIBLE in the deploy row + dashboard — set_state writes state
            # only, which previously left load_model failures with an empty
            # error_message and no clue why the container never started.
            async with self._db.acquire() as conn:
                async with conn.transaction():
                    await self._inventory.release_gpu(
                        node_id, gpu_required, tx=conn,
                    )
                    await self._deploys.update_state(
                        deploy["id"], "FAILED", tx=conn,
                        error_message=f"load_model error: {e}",
                    )
            return

        # The worker can return a CommandResult with status='failed' (readiness
        # probe timeout, pull failed) WITHOUT raising — treat that as a load
        # failure: release the GPU + mark FAILED, do NOT report RUNNING.
        if getattr(result, "status", None) == "failed":
            detail = getattr(result, "detail", "") or "worker reported load failure"
            logger.error(
                "linker: load_model returned status=failed for deploy=%s: %s",
                deploy["id"], detail,
            )
            async with self._db.acquire() as conn:
                async with conn.transaction():
                    await self._inventory.release_gpu(
                        node_id, gpu_required, tx=conn,
                    )
                    await self._deploys.update_state(
                        deploy["id"], "FAILED", tx=conn,
                        error_message=f"load_model failed on worker: {detail}",
                    )
            return

        # Model loaded on the worker. Promote DEPLOYING → RUNNING.
        async with self._db.acquire() as conn:
            await self._deploys.set_state(deploy["id"], "RUNNING", tx=conn)
        # Publish the inference endpoint (the node's CP-reachable advertise_url,
        # NOT the worker's 127.0.0.1 loopback) so the data plane can route.
        try:
            async with self._db.acquire() as conn:
                advertise = await conn.fetchval(
                    "SELECT advertise_url FROM compute_inventory WHERE id=$1",
                    node_id,
                )
            if advertise:
                await self._deploys.update_endpoint(deploy["id"], advertise)
            else:
                logger.warning(
                    "linker: node=%s has no advertise_url; deploy=%s endpoint "
                    "not set (inference unreachable)",
                    node_id, deploy["id"],
                )
        except Exception:
            logger.exception(
                "linker: failed to set endpoint for deploy=%s", deploy["id"],
            )

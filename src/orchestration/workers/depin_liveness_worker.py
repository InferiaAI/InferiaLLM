"""DePIN deployment liveness reconciler.

DePIN providers (Nosana/Akash) run the inference engine as a single externally
managed container. When that container exits, InferiaLLM does NOT reliably learn
about it: ``provision_direct_node`` marks the deployment ``RUNNING`` once and the
sidecar watchdog only flips it FAILED when the Nosana *deployment* API reports a
terminal status (a 60s poll, with a <20min FAILED / >=20min auto-redeploy split).
If the watchdog isn't attached, or its terminal heartbeat is missed, the
deployment is stuck ``RUNNING`` with a dead (503) endpoint forever — observed
live (a deploy could not even be re-started: ``cannot start deployment in state
RUNNING``).

This worker closes that gap from the orchestration side: it periodically polls
the ACTUAL Nosana job state for every RUNNING direct-adapter deployment and, when
the job is terminal, marks the deployment FAILED, deprovisions the external
(paid) job, and terminates the placeholder node.

Race-safety (the SIMPLE-EXTEND auto-redeploy window): a redeploy swaps the
node's ``provider_instance_id`` to a NEW running job (via the sidecar swap
heartbeat). To avoid failing a deployment that is actually recovering, this
worker:
- only acts on a CONFIRMED terminal state — ``get_node_status`` returns
  ``"unknown"`` on any error/non-200 and for providers that don't implement it
  (the base default), so a transient poll failure NEVER fails a healthy
  deployment;
- **re-reads the node after the status check** and bails if the bound
  ``provider_instance_id`` changed (a redeploy happened → recovering); and
- uses ``update_state_if(expected="RUNNING")`` so the FAILED transition is a
  no-op (and fires no event, and skips deprovision) if any other flow already
  moved the deployment. A small residual window remains (between the status read
  and the swap heartbeat landing) — bounded to at most one spurious FAILED per
  redeploy, never cancelling a live job.
"""
import asyncio
import logging

from orchestration.provisioning.engine.registry import (
    get_adapter,
    is_direct_provision_provider,
    _deprovision_direct_node,
)

logger = logging.getLogger("depin-liveness-worker")

# Normalized job states that mean the container is gone for good.
_TERMINAL_STATES = {"COMPLETED", "STOPPED", "QUIT", "FAILED"}


class DepinLivenessWorker:
    """Periodically reconciles RUNNING direct-adapter (DePIN) deployments
    against the real provider job state, failing + deprovisioning dead ones."""

    def __init__(
        self,
        *,
        deploys,
        inventory,
        pool_repo,
        interval_seconds: int = 45,
        get_adapter_fn=get_adapter,
    ) -> None:
        self.deploys = deploys
        self.inventory = inventory
        self.pool_repo = pool_repo
        self.interval = interval_seconds
        self._get_adapter = get_adapter_fn

    async def run(self) -> None:
        logger.info("DePIN liveness worker started (interval=%ss)", self.interval)
        while True:
            try:
                await self.reconcile_once()
            except Exception:
                logger.exception("DePIN liveness reconcile tick failed")
            await asyncio.sleep(self.interval)

    async def reconcile_once(self) -> None:
        running = await self.deploys.list_by_state("RUNNING")
        for d in running or []:
            try:
                await self._check_one(d)
            except Exception:
                logger.exception(
                    "DePIN liveness: error checking deployment %s",
                    d.get("deployment_id"),
                )

    async def _check_one(self, d: dict) -> None:
        pool_id = d.get("pool_id")
        if not pool_id:
            return

        pool = await self.pool_repo.get(pool_id)
        if not pool:
            return
        provider = pool.get("provider")
        if not is_direct_provision_provider(provider):
            return  # only DePIN/direct-adapter deployments

        node_id = d.get("target_node_id")
        if not node_id:
            node_ids = d.get("node_ids") or []
            node_id = node_ids[0] if node_ids else None
        if not node_id:
            return

        node = await self.inventory.get_node_by_id(node_id)
        if not node:
            return
        pii = node.get("provider_instance_id")
        if not pii or str(pii).startswith("placeholder:"):
            return  # never provisioned an external job yet

        cred = pool.get("provider_credential_name")
        adapter = self._get_adapter(provider)
        status = await adapter.get_node_status(
            provider_instance_id=pii,
            provider_credential_name=cred,
        )

        if status not in _TERMINAL_STATES:
            return  # RUNNING / QUEUED / unknown -> leave it alone

        deploy_id = d.get("deployment_id")

        # Read-after-confirm: a SIMPLE-EXTEND redeploy swaps the node's job id.
        # If the bound provider_instance_id changed since our status check, the
        # deployment is recovering on a new job — do NOT fail it.
        fresh = await self.inventory.get_node_by_id(node_id)
        if not fresh or fresh.get("provider_instance_id") != pii:
            logger.info(
                "DePIN liveness: node %s job changed since check (redeploy?); "
                "skipping deployment %s",
                node_id,
                deploy_id,
            )
            return

        logger.warning(
            "DePIN deployment %s is RUNNING but its job %s is %s; "
            "marking FAILED + deprovisioning (liveness reconcile)",
            deploy_id,
            pii,
            status,
        )
        # Atomic + idempotent: only fail if STILL RUNNING. If another flow
        # (sidecar watchdog / cancel) already transitioned it, this is a no-op
        # (no event re-fire) and we skip the teardown.
        flipped = await self.deploys.update_state_if(
            deploy_id,
            "RUNNING",
            "FAILED",
            error_message=(
                f"DePIN job {status}: the container exited on the provider node "
                f"(reconciled by liveness worker)"
            ),
        )
        if not flipped:
            return

        try:
            await _deprovision_direct_node(fresh, pool_credential_name=cred)
        except Exception:
            logger.exception(
                "DePIN liveness: deprovision failed for %s (job %s)",
                deploy_id,
                pii,
            )
        try:
            await self.inventory.mark_terminated(node_id)
        except Exception:
            logger.exception(
                "DePIN liveness: mark_terminated failed for node %s", node_id
            )

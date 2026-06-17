"""DePIN deployment liveness + resume reconciler.

DePIN providers (Nosana/Akash) run the inference engine as a single externally
managed container, driven by a fire-and-forget background coroutine
(``provision_direct_node``): PENDING_NODE → DEPLOYING (model loading) → RUNNING
(endpoint serving). That coroutine is the only thing advancing a DePIN deploy,
and it does NOT survive a control-plane restart. So two failure classes leave a
deploy wedged with no one watching it:

1. **Dead RUNNING** — the external container exits but nothing flips the
   deployment FAILED (the sidecar watchdog wasn't attached / missed the
   terminal heartbeat). The deployment is stuck RUNNING with a dead (503)
   endpoint forever.
2. **Orphaned non-terminal** — the CP restarts while the deploy is mid-flight
   (PENDING_NODE during provisioning, or DEPLOYING during the multi-minute
   image-pull + model-load). The probe coroutine dies and the deploy is stuck
   in that state forever (a deploy that is actually serving never reaches
   RUNNING; a deploy whose container crashed never reaches FAILED).

This worker is the durable backstop for BOTH. It periodically polls the ACTUAL
provider job state (and, for DePIN, the inference endpoint's ``/health``) and
reconciles every direct-adapter deployment:

* RUNNING  → FAILED + deprovision, if the job is terminal (dead RUNNING).
* DEPLOYING→ RUNNING when the endpoint actually serves (resume the dead probe);
             FAILED + deprovision if the job went terminal during load;
             FAILED if it has been loading far longer than any cold start.
* PENDING_NODE → FAILED (+ best-effort deprovision) only if it has been stuck
             far longer than any normal provision (the coroutine is surely
             dead); below that threshold the in-process coroutine is likely
             still working, so we DON'T interfere.

Race-safety: every transition uses ``update_state_if(expected=...)`` so it is a
no-op (and fires no teardown) if the live coroutine / cancel flow already moved
the deployment — the worker and a live probe can both reconcile a DEPLOYING
deploy without conflict. Terminal-state fails re-read the node first and bail if
its ``provider_instance_id`` changed (a SIMPLE-EXTEND redeploy is recovering).
``get_node_status`` returns ``"unknown"`` on any error, so a transient poll
failure never fails a healthy deployment.
"""
import asyncio
import logging
from datetime import datetime, timezone

import aiohttp

from orchestration.provisioning.engine.registry import (
    get_adapter,
    is_direct_provision_provider,
    _deprovision_direct_node,
)

logger = logging.getLogger("depin-liveness-worker")

# Normalized job states that mean the container is gone for good.
_TERMINAL_STATES = {"COMPLETED", "STOPPED", "QUIT", "FAILED"}


class DepinLivenessWorker:
    """Periodically reconciles direct-adapter (DePIN) deployments against the
    real provider job state + endpoint, recovering both dead-RUNNING and
    restart-orphaned (PENDING_NODE / DEPLOYING) deployments."""

    def __init__(
        self,
        *,
        deploys,
        inventory,
        pool_repo,
        interval_seconds: int = 45,
        # A DEPLOYING deploy whose endpoint has not served within this window is
        # force-resolved (default 35min > worst-case 9GB pull + model load +
        # the in-process probe's 1800s timeout, so a LIVE probe always acts
        # first and this only fires for orphaned deploys).
        deploying_max_seconds: int = 2100,
        # A PENDING_NODE deploy older than this is treated as orphaned (normal
        # PENDING_NODE lasts <~300s, the wait_for_ready window); below it the
        # in-process coroutine is likely still provisioning — don't interfere.
        pending_max_seconds: int = 900,
        get_adapter_fn=get_adapter,
    ) -> None:
        self.deploys = deploys
        self.inventory = inventory
        self.pool_repo = pool_repo
        self.interval = interval_seconds
        self.deploying_max_seconds = deploying_max_seconds
        self.pending_max_seconds = pending_max_seconds
        self._get_adapter = get_adapter_fn

    async def run(self) -> None:
        logger.info(
            "DePIN reconciler started (interval=%ss, deploying_max=%ss, "
            "pending_max=%ss)",
            self.interval,
            self.deploying_max_seconds,
            self.pending_max_seconds,
        )
        while True:
            try:
                await self.reconcile_once()
            except Exception:
                logger.exception("DePIN reconcile tick failed")
            await asyncio.sleep(self.interval)

    async def reconcile_once(self) -> None:
        await self._sweep("RUNNING", self._check_running)
        await self._sweep("DEPLOYING", self._check_deploying)
        await self._sweep("PENDING_NODE", self._check_pending)

    async def _sweep(self, state: str, handler) -> None:
        rows = await self.deploys.list_by_state(state)
        for d in rows or []:
            try:
                await handler(d)
            except Exception:
                logger.exception(
                    "DePIN reconcile: error checking %s deployment %s",
                    state,
                    d.get("deployment_id"),
                )

    # ------------------------------------------------------------------ helpers
    async def _resolve(self, d: dict):
        """Resolve the DePIN context for a deployment row, or None if it is not
        a reconcilable direct-adapter deploy. ``node``/``pii`` may be None (a
        not-yet-finalized placeholder)."""
        pool_id = d.get("pool_id")
        if not pool_id:
            return None
        pool = await self.pool_repo.get(pool_id)
        if not pool:
            return None
        provider = pool.get("provider")
        if not is_direct_provision_provider(provider):
            return None
        node_id = d.get("target_node_id")
        if not node_id:
            node_ids = d.get("node_ids") or []
            node_id = node_ids[0] if node_ids else None
        node = await self.inventory.get_node_by_id(node_id) if node_id else None
        pii = node.get("provider_instance_id") if node else None
        if pii and str(pii).startswith("placeholder:"):
            pii = None
        return {
            "provider": provider,
            "cred": pool.get("provider_credential_name"),
            "node_id": node_id,
            "node": node,
            "pii": pii,
            "adapter": self._get_adapter(provider),
        }

    @staticmethod
    def _age_seconds(d: dict):
        ts = d.get("updated_at")
        if ts is None:
            return None
        try:
            if getattr(ts, "tzinfo", None) is None:
                ts = ts.replace(tzinfo=timezone.utc)
            return (datetime.now(timezone.utc) - ts).total_seconds()
        except Exception:
            return None

    async def _endpoint_serving(self, endpoint) -> bool:
        """One-shot: does the deployment endpoint answer HTTP 200? (/health is
        unauthenticated on the engines; /v1/models is the fallback.)"""
        if not endpoint or not str(endpoint).startswith("http"):
            return False
        base = str(endpoint).rstrip("/")
        try:
            async with aiohttp.ClientSession() as session:
                for path in ("/health", "/v1/models"):
                    try:
                        async with session.get(
                            base + path,
                            timeout=aiohttp.ClientTimeout(total=10),
                        ) as resp:
                            if resp.status == 200:
                                return True
                    except Exception:
                        pass
        except Exception:
            pass
        return False

    async def _fail_and_teardown(
        self, *, deploy_id, expected_state, node_id, node, pii, cred, message
    ) -> bool:
        """Atomically FAIL the deploy (only if still in expected_state), then —
        if we won the transition — deprovision the external (paid) job and
        terminate the placeholder node. Mirrors the dead-RUNNING teardown:
        deprovision + mark_terminated, never release_gpu (the cancel/refcount
        flow owns GPU release — see the atomic-refcount MEMORY note)."""
        flipped = await self.deploys.update_state_if(
            deploy_id, expected_state, "FAILED", error_message=message
        )
        if not flipped:
            return False
        if pii and node:
            try:
                await _deprovision_direct_node(node, pool_credential_name=cred)
            except Exception:
                logger.exception(
                    "DePIN reconcile: deprovision failed for %s", deploy_id
                )
        if node_id:
            try:
                await self.inventory.mark_terminated(node_id)
            except Exception:
                logger.exception(
                    "DePIN reconcile: mark_terminated failed for node %s", node_id
                )
        return True

    # ------------------------------------------------------------------ checks
    async def _check_running(self, d: dict) -> None:
        """Dead-RUNNING: a RUNNING deploy whose external job is terminal."""
        r = await self._resolve(d)
        if r is None or not r["pii"]:
            return
        deploy_id = d.get("deployment_id")
        status = await r["adapter"].get_node_status(
            provider_instance_id=r["pii"], provider_credential_name=r["cred"]
        )
        if status not in _TERMINAL_STATES:
            return
        # Read-after-confirm: a SIMPLE-EXTEND redeploy swaps the node's job id;
        # if it changed, the deployment is recovering on a new job — don't fail.
        fresh = await self.inventory.get_node_by_id(r["node_id"])
        if not fresh or fresh.get("provider_instance_id") != r["pii"]:
            logger.info(
                "DePIN reconcile: node %s job changed since check (redeploy?); "
                "skipping RUNNING deployment %s",
                r["node_id"],
                deploy_id,
            )
            return
        logger.warning(
            "DePIN deployment %s RUNNING but job %s is %s; FAILED + deprovision",
            deploy_id,
            r["pii"],
            status,
        )
        await self._fail_and_teardown(
            deploy_id=deploy_id,
            expected_state="RUNNING",
            node_id=r["node_id"],
            node=fresh,
            pii=r["pii"],
            cred=r["cred"],
            message=(
                f"DePIN job {status}: the container exited on the provider node "
                f"(reconciled by liveness worker)"
            ),
        )

    async def _check_deploying(self, d: dict) -> None:
        """Resume/recover a DEPLOYING deploy whose probe coroutine may be dead."""
        r = await self._resolve(d)
        if r is None:
            return
        deploy_id = d.get("deployment_id")
        age = self._age_seconds(d)

        if not r["pii"]:
            # DEPLOYING is set only AFTER finalize (which sets pii), so no pii
            # here is anomalous — fail it if it's been stuck past the window.
            if age is not None and age > self.deploying_max_seconds:
                await self._fail_and_teardown(
                    deploy_id=deploy_id, expected_state="DEPLOYING",
                    node_id=r["node_id"], node=r["node"], pii=None, cred=r["cred"],
                    message=(
                        "provisioning did not complete (no provider job; "
                        "control-plane restart during provisioning); please retry"
                    ),
                )
            return

        status = await r["adapter"].get_node_status(
            provider_instance_id=r["pii"], provider_credential_name=r["cred"]
        )

        if status in _TERMINAL_STATES:
            fresh = await self.inventory.get_node_by_id(r["node_id"])
            if not fresh or fresh.get("provider_instance_id") != r["pii"]:
                return  # redeploy swapped the job; recovering
            logger.warning(
                "DePIN reconcile: DEPLOYING deployment %s job %s is %s; "
                "FAILED + deprovision",
                deploy_id, r["pii"], status,
            )
            await self._fail_and_teardown(
                deploy_id=deploy_id, expected_state="DEPLOYING",
                node_id=r["node_id"], node=fresh, pii=r["pii"], cred=r["cred"],
                message=(
                    f"DePIN job {status}: the container exited during model load "
                    f"(reconciled by resume sweep)"
                ),
            )
            return

        # Job alive — has the endpoint started serving?
        if await self._endpoint_serving(d.get("endpoint")):
            if await self.deploys.update_state_if(deploy_id, "DEPLOYING", "RUNNING"):
                logger.info(
                    "DePIN reconcile: DEPLOYING deployment %s endpoint serving "
                    "-> RUNNING (resumed after probe loss)",
                    deploy_id,
                )
            return

        # Still loading. Only force-resolve if it has been DEPLOYING far longer
        # than any cold start — then it is genuinely stuck, so FAIL it (the
        # user can retry) rather than leave it RUNNING with a dead endpoint.
        if age is not None and age > self.deploying_max_seconds:
            logger.warning(
                "DePIN reconcile: DEPLOYING deployment %s endpoint never served "
                "in %ss; FAILED + deprovision",
                deploy_id, int(self.deploying_max_seconds),
            )
            await self._fail_and_teardown(
                deploy_id=deploy_id, expected_state="DEPLOYING",
                node_id=r["node_id"], node=r["node"], pii=r["pii"], cred=r["cred"],
                message=(
                    f"endpoint did not become ready within "
                    f"{int(self.deploying_max_seconds)}s (reconciled by resume "
                    f"sweep after control-plane restart)"
                ),
            )

    async def _check_pending(self, d: dict) -> None:
        """Fail a PENDING_NODE DePIN deploy that has been stuck far longer than
        any normal provision (its coroutine is surely dead). Below the
        threshold the live coroutine is likely still working — leave it."""
        age = self._age_seconds(d)
        if age is None or age <= self.pending_max_seconds:
            return
        r = await self._resolve(d)
        if r is None:
            return
        deploy_id = d.get("deployment_id")
        logger.warning(
            "DePIN reconcile: PENDING_NODE deployment %s stuck %ss "
            "(provision coroutine lost?); FAILED",
            deploy_id, int(age),
        )
        await self._fail_and_teardown(
            deploy_id=deploy_id, expected_state="PENDING_NODE",
            node_id=r["node_id"], node=r["node"], pii=r["pii"], cred=r["cred"],
            message=(
                "provisioning did not complete (control-plane restart during "
                "provisioning); please retry"
            ),
        )

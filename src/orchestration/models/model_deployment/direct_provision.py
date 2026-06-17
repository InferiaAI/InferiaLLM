"""Shared background helper that provisions a DePIN/direct-adapter node
(Nosana/Akash/k8s) and drives a deployment to RUNNING.

This is the live replacement for the now-dead legacy ``worker.py`` DePIN tail
(``ModelDeploymentWorker._provision_and_link``). It reuses the existing
:class:`ProviderAdapter` interface and reads the pre-created placeholder node —
finalizing it by ``node_id`` rather than calling ``register_node`` (which would
create a duplicate inventory row).

Designed to run fire-and-forget as a standalone background coroutine, so it
owns its OWN try/except and never lets an exception escape.

Wiring it into ``place_and_provision`` is a later task; this module only
implements the coroutine.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time

import aiohttp

from orchestration.provisioning.engine.registry import get_adapter

log = logging.getLogger(__name__)

# Endpoints the readiness poll may return that are NOT real access URLs: an
# empty value, a "<id>-ready" marker, or the confidential-compute placeholder.
# When the adapter returns one of these we fall back to node_spec["expose_url"]
# (mirrors the legacy DePIN tail sentinel logic plus the Nosana confidential
# job sentinel).
_CONFIDENTIAL_SENTINEL = "job-running-confidential"

# Provider job states that mean the container is gone for good (mirrors the
# DePIN liveness worker). If the job hits one of these while we are waiting for
# the endpoint to serve, the model crashed — fail fast instead of waiting out
# the full readiness timeout.
_TERMINAL_JOB_STATES = {"COMPLETED", "STOPPED", "QUIT", "FAILED"}

# How often to poll the endpoint / job while waiting for it to serve.
_READY_PROBE_INTERVAL_SECONDS = 15


async def _wait_endpoint_serving(
    *,
    adapter,
    expose_url: str,
    provider_instance_id: str,
    cred_name,
    deploy_id,
    deps,
    timeout: int,
) -> str:
    """Poll a DePIN deployment's public endpoint until it actually serves.

    Returns one of:
      - ``"ready"``     — the endpoint returned HTTP 200 (model is serving)
      - ``"crashed"``   — the provider job went terminal before serving
      - ``"cancelled"`` — the deployment left DEPLOYING (cancelled/deleted)
      - ``"timeout"``   — neither served nor crashed within ``timeout``

    DePIN cold start is slow (large image pull + model load), so this can run
    for many minutes; it is invoked from the fire-and-forget provisioning
    coroutine, which already blocks on ``wait_for_ready``.
    """
    base = expose_url.rstrip("/")
    start = time.monotonic()
    async with aiohttp.ClientSession() as session:
        while True:
            # Cancellation: stop probing a deploy that is no longer DEPLOYING.
            d = await deps.deploys.get(deploy_id)
            if not d or d.get("state") != "DEPLOYING":
                return "cancelled"

            # Fail fast: a terminal job will never serve.
            try:
                status = await adapter.get_node_status(
                    provider_instance_id=provider_instance_id,
                    provider_credential_name=cred_name,
                )
            except Exception:  # noqa: BLE001 — never let a poll error abort
                status = "unknown"
            if status in _TERMINAL_JOB_STATES:
                return "crashed"

            # /health is unauthenticated on the inference engines; /v1/models is
            # the fallback (may be 401 when an api-key is set, which is not 200,
            # so it never falsely succeeds).
            for path in ("/health", "/v1/models"):
                try:
                    async with session.get(
                        base + path,
                        timeout=aiohttp.ClientTimeout(total=10),
                    ) as resp:
                        if resp.status == 200:
                            return "ready"
                except Exception:  # noqa: BLE001 — endpoint not up yet
                    pass

            if time.monotonic() - start > timeout:
                return "timeout"
            await asyncio.sleep(_READY_PROBE_INTERVAL_SECONDS)


def _is_sentinel_endpoint(expose_url) -> bool:
    """True when ``expose_url`` is a sentinel/empty rather than a real URL."""
    if not expose_url:
        return True
    return expose_url.endswith("-ready") or expose_url == _CONFIDENTIAL_SENTINEL


def _build_metadata(d: dict) -> dict:
    """Assemble the adapter job metadata from a deployment row.

    Uses ``configuration`` directly (the Unified Schema), json-decoding it if
    it arrived as a string, then inject the model identifiers (``model_id``
    from ``inference_model``, plus ``model_name`` / ``engine``) for the
    provider's job_builder. Tolerant of missing keys.

    Note: the HuggingFace token is expected to already be baked into
    ``configuration.env.HF_TOKEN`` at deploy-create time (NosanaAdapter reads
    ``metadata["env"]["HF_TOKEN"]``). This function deliberately does NOT
    re-resolve it — resolving it here would bypass the stored encrypted
    credential and risk a stale value from an in-memory settings snapshot.
    """
    metadata: dict = {}

    config = d.get("configuration")
    if config:
        if isinstance(config, str):
            try:
                config = json.loads(config)
            except json.JSONDecodeError:
                config = {}
        if isinstance(config, dict):
            metadata = config

    # Inject model identifiers for job_builder (API key security).
    if d.get("inference_model"):
        metadata["model_id"] = d["inference_model"]
    if d.get("model_name"):
        metadata["model_name"] = d["model_name"]
    if d.get("engine"):
        metadata["engine"] = d["engine"]

    return metadata


async def provision_direct_node(
    *,
    deploy_id,
    node_id,
    pool_row,
    pool_meta,
    provider,
    gpu_per_replica,
    deps,
) -> None:
    """Provision a DePIN/direct-adapter node in the background and drive the
    deployment to RUNNING (or FAILED). Fills in the pre-created placeholder
    node; reuses the ProviderAdapter interface. Mirrors the legacy
    DePIN tail design. Safe to run fire-and-forget (owns its try/except)."""

    # ---- Resolve the provider adapter INSIDE the guarded region ----
    # get_adapter / get_capabilities run first so an unknown-provider
    # ValueError is caught and marks the deployment FAILED rather than
    # escaping the coroutine.  At this point no external instance has been
    # created yet, so we do NOT attempt deprovision on this failure path.
    try:
        adapter = get_adapter(provider)
        caps = adapter.get_capabilities()
    except Exception as e:  # noqa: BLE001
        log.exception(
            "provision_direct_node: failed to resolve provider %s for "
            "deployment %s: %s",
            provider,
            deploy_id,
            e,
        )
        try:
            await deps.deploys.update_state(
                deploy_id, "FAILED", error_message=str(e)
            )
        except Exception as state_err:  # noqa: BLE001
            log.warning(
                "provision_direct_node: failed to mark deployment %s FAILED "
                "(provider resolution error): %s",
                deploy_id,
                state_err,
            )
        return

    # ---- Early-abort: check the deployment is still deployable ----
    d = await deps.deploys.get(deploy_id)
    if d is None:
        log.warning(
            "provision_direct_node: deployment %s not found (deleted); "
            "aborting before provisioning",
            deploy_id,
        )
        return
    if d.get("state") != "PENDING_NODE":
        log.warning(
            "provision_direct_node: deployment %s is in state %s (not "
            "PENDING_NODE); aborting before provisioning to avoid creating a "
            "paid instance for an already-cancelled deploy",
            deploy_id,
            d.get("state"),
        )
        return

    # ---- Validate pool configuration ----
    if not pool_row.get("allowed_gpu_types"):
        msg = "Pool has no GPU type configured (allowed_gpu_types empty)"
        log.error(
            "provision_direct_node: deployment %s aborting — %s", deploy_id, msg
        )
        try:
            await deps.deploys.update_state(
                deploy_id, "FAILED", error_message=msg
            )
        except Exception as state_err:  # noqa: BLE001
            log.warning(
                "provision_direct_node: failed to mark deployment %s FAILED "
                "(empty gpu types): %s",
                deploy_id,
                state_err,
            )
        return

    cred_name = pool_row.get("provider_credential_name")
    provider_instance_id = None
    try:
        metadata = _build_metadata(d)

        node_spec = await adapter.provision_node(
            provider_resource_id=pool_row["allowed_gpu_types"][0],
            pool_id=pool_row["provider_pool_id"],
            metadata=metadata,
            provider_credential_name=cred_name,
        )

        # Simulation short-circuit (provider-agnostic): no external instance was
        # actually created, so just mark the deploy RUNNING and return.
        if node_spec.get("metadata", {}).get("mode") == "simulation":
            log.info(
                "provision_direct_node: deployment %s provider=%s simulation mode "
                "-> RUNNING",
                deploy_id,
                provider,
            )
            await deps.deploys.update_state(deploy_id, "RUNNING")
            return

        # Validate that the adapter returned a provider_instance_id; without it
        # we cannot track, finalize, or deprovision the external instance.
        provider_instance_id = node_spec.get("provider_instance_id")
        if not provider_instance_id:
            raise RuntimeError(
                f"adapter returned no provider_instance_id for deploy {deploy_id}"
            )

        # ---- Universal readiness poll ----
        expose_url = await adapter.wait_for_ready(
            provider_instance_id=provider_instance_id,
            timeout=caps.readiness_timeout_seconds,
            provider_credential_name=cred_name,
        )

        # Normalize: a sentinel (empty, a "...-ready" marker, or the confidential
        # "job-running-confidential" placeholder) means the adapter has no real
        # endpoint to hand back; fall back to the node_spec's expose_url.
        if _is_sentinel_endpoint(expose_url):
            expose_url = node_spec.get("expose_url")

        # ---- Cancellation guard ----
        # The deployment may have been cancelled/deleted while we were waiting
        # for the external node to become ready. If so, do NOT finalize or mark
        # RUNNING — best-effort deprovision the just-created external instance.
        #
        # NOTE: we intentionally do NOT call release_gpu or mark_terminated
        # here.  The placeholder GPU release and node termination are owned by
        # the cancel/delete flow that changed the state; releasing them here
        # would risk a double-release (see the project's atomic-refcount guard
        # in the MEMORY note "Atomic state-claim guards refcount release").
        # This path only deprovisions the external (paid) instance.
        d2 = await deps.deploys.get(deploy_id)
        if not d2 or d2.get("state") != "PENDING_NODE":
            if d2 is None:
                log.warning(
                    "provision_direct_node: deployment %s was deleted during "
                    "provisioning; aborting + deprovisioning external instance %s",
                    deploy_id,
                    provider_instance_id,
                )
            else:
                log.warning(
                    "provision_direct_node: deployment %s state changed to %s "
                    "during provisioning; aborting + deprovisioning external "
                    "instance %s",
                    deploy_id,
                    d2.get("state"),
                    provider_instance_id,
                )
            await _best_effort_deprovision(adapter, provider_instance_id, cred_name)
            return

        # ---- Finalize the placeholder node ----
        ok = await deps.inventory.finalize_direct_node(
            node_id=node_id,
            provider_instance_id=provider_instance_id,
            hostname=node_spec.get("hostname", ""),
            gpu_total=node_spec.get("gpu_total", 0),
            vcpu_total=node_spec.get("vcpu_total", 0),
            ram_gb_total=node_spec.get("ram_gb_total", 0),
            node_class=node_spec.get("node_class", "gpu"),
            metadata=node_spec.get("metadata", {}),
            expose_url=expose_url,
        )
        if not ok:
            # The placeholder was finalized/terminated/cancelled concurrently
            # (the deploy was cancelled or deleted). Treat as cancellation:
            # best-effort deprovision and return WITHOUT marking RUNNING.
            #
            # NOTE: same as the cancellation-guard path above — do NOT call
            # release_gpu or mark_terminated here.  The cancel/delete flow
            # that removed the placeholder owns those transitions; releasing
            # here would double-release (atomic-refcount guard).  We only
            # deprovision the external (paid) instance.
            log.warning(
                "provision_direct_node: placeholder node %s for deployment %s gone "
                "(finalize returned False); deprovisioning external instance %s",
                node_id,
                deploy_id,
                provider_instance_id,
            )
            await _best_effort_deprovision(adapter, provider_instance_id, cred_name)
            return

        if expose_url:
            await deps.deploys.update_endpoint(
                deploy_id, expose_url, model_name=d.get("model_name")
            )

        # ---- Endpoint readiness gate ----
        # DePIN cold start is slow (large image pull + model load). For providers
        # whose public endpoint is reachable from the control plane, do NOT mark
        # RUNNING until the endpoint actually serves — otherwise the dashboard
        # shows RUNNING while the endpoint 503s and the playground returns
        # "Upstream provider returned an error". Other providers keep the
        # original "RUNNING once scheduled" behavior.
        probeable = (
            getattr(caps, "endpoint_http_probeable", False)
            and expose_url
            and not _is_sentinel_endpoint(expose_url)
            and str(expose_url).startswith("http")
        )
        if not probeable:
            await deps.deploys.update_state(deploy_id, "RUNNING")
            log.info(
                "provision_direct_node: deployment %s provider=%s node %s RUNNING "
                "(instance=%s)",
                deploy_id,
                provider,
                node_id,
                provider_instance_id,
            )
            return

        # PENDING_NODE -> DEPLOYING (model loading). Guarded so a concurrent
        # cancel/delete is not clobbered.
        if not await deps.deploys.update_state_if(
            deploy_id, "PENDING_NODE", "DEPLOYING"
        ):
            log.warning(
                "provision_direct_node: deployment %s left PENDING_NODE before "
                "readiness probe (cancelled?); deprovisioning %s",
                deploy_id,
                provider_instance_id,
            )
            await _best_effort_deprovision(adapter, provider_instance_id, cred_name)
            return

        timeout = getattr(caps, "endpoint_ready_timeout_seconds", 1800)
        result = await _wait_endpoint_serving(
            adapter=adapter,
            expose_url=expose_url,
            provider_instance_id=provider_instance_id,
            cred_name=cred_name,
            deploy_id=deploy_id,
            deps=deps,
            timeout=timeout,
        )
        if result == "crashed":
            # Surface a clear cause; the except block below marks FAILED +
            # releases the GPU + deprovisions (uniform teardown).
            raise RuntimeError(
                f"endpoint never served: the provider job {provider_instance_id} "
                f"exited during model load (container crashed on the node)"
            )
        if result == "cancelled":
            log.warning(
                "provision_direct_node: deployment %s cancelled during readiness "
                "probe; deprovisioning %s",
                deploy_id,
                provider_instance_id,
            )
            await _best_effort_deprovision(adapter, provider_instance_id, cred_name)
            return
        if result == "timeout":
            # Slow node that never served within the window: do NOT fail it (it
            # may still come up). Mark RUNNING and let the DePIN liveness
            # reconciler flip it FAILED if the job later goes terminal.
            log.warning(
                "provision_direct_node: deployment %s endpoint %s did not serve "
                "within %ss; marking RUNNING anyway (liveness worker reconciles "
                "if the job dies)",
                deploy_id,
                expose_url,
                timeout,
            )

        # ready or timeout -> RUNNING (guarded against a concurrent cancel).
        if not await deps.deploys.update_state_if(
            deploy_id, "DEPLOYING", "RUNNING"
        ):
            log.warning(
                "provision_direct_node: deployment %s left DEPLOYING before "
                "RUNNING (cancelled?); deprovisioning %s",
                deploy_id,
                provider_instance_id,
            )
            await _best_effort_deprovision(adapter, provider_instance_id, cred_name)
            return
        log.info(
            "provision_direct_node: deployment %s provider=%s node %s RUNNING "
            "(instance=%s, probe=%s)",
            deploy_id,
            provider,
            node_id,
            provider_instance_id,
            result,
        )

    except Exception as e:  # noqa: BLE001 — background task must never escape
        log.exception(
            "provision_direct_node failed for deployment %s provider=%s: %s",
            deploy_id,
            provider,
            e,
        )
        # Record the failure. ``set_state`` cannot carry an error_message, so
        # use ``update_state`` (the observable, error-message-aware transition)
        # exactly as the legacy worker DePIN FAILED path did.
        try:
            await deps.deploys.update_state(
                deploy_id, "FAILED", error_message=str(e)
            )
        except Exception as state_err:  # noqa: BLE001
            log.warning(
                "provision_direct_node: failed to mark deployment %s FAILED: %s",
                deploy_id,
                state_err,
            )

        # Release the placeholder's reserved GPU and mark it terminated so the
        # reaper/refcount logic frees it (best-effort).
        try:
            await deps.inventory.release_gpu(node_id, gpu_per_replica or 0)
        except Exception as rel_err:  # noqa: BLE001
            log.warning(
                "provision_direct_node: release_gpu failed for node %s: %s",
                node_id,
                rel_err,
            )
        try:
            await deps.inventory.mark_terminated(node_id)
        except Exception as term_err:  # noqa: BLE001
            log.warning(
                "provision_direct_node: mark_terminated failed for node %s: %s",
                node_id,
                term_err,
            )

        # If we got as far as creating an external instance, tear it down so it
        # doesn't leak / keep billing.
        if provider_instance_id:
            await _best_effort_deprovision(adapter, provider_instance_id, cred_name)


async def _best_effort_deprovision(adapter, provider_instance_id, cred_name) -> None:
    """Deprovision an external instance, swallowing any error (cleanup path)."""
    if not provider_instance_id:
        return
    try:
        await adapter.deprovision_node(
            provider_instance_id=provider_instance_id,
            provider_credential_name=cred_name,
        )
    except Exception as cleanup_err:  # noqa: BLE001
        log.warning(
            "provision_direct_node: best-effort deprovision of %s failed: %s",
            provider_instance_id,
            cleanup_err,
        )

"""
Adapter for self-hosted (inferia-worker) pools.

Worker pools have no DePIN/cloud SDK to talk to: each worker registers
itself with the control plane via the bootstrap-token + WS-channel flow
in services/worker_controller/. The adapter therefore implements only the
minimum surface compute_pool_engine + the createpool flow require —
discovery and provisioning are no-ops; the actual node lifecycle is
driven by the WorkerController instead of this adapter.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from services.orchestration.adapter_engine.base import (
    AdapterType,
    PricingModel,
    ProviderAdapter,
    ProviderCapabilities,
)


class WorkerAdapter(ProviderAdapter):
    """Pass-through adapter for inferia-worker pools."""

    ADAPTER_TYPE = AdapterType.ON_PREM

    CAPABILITIES = ProviderCapabilities(
        supports_log_streaming=True,
        supports_confidential_compute=False,
        supports_spot_instances=False,
        supports_multi_gpu=True,
        is_ephemeral=False,
        requires_readiness_poll=False,
        readiness_timeout_seconds=180,
        polling_interval_seconds=5,
        requires_sidecar=False,
        # Workers self-provision (they're already running before they
        # register), so the orchestrator never calls provision_node().
        supports_direct_provisioning=False,
        pricing_model=PricingModel.FIXED,
        features={
            "self_registered_nodes": True,
            "agent_based": True,
            "no_cloud_credentials": True,
        },
    )

    def __init__(self):
        pass

    async def discover_resources(self) -> List[Dict]:
        # Worker pools accept any GPU the host has. The dashboard's
        # "New pool → Self-hosted" flow already shows a placeholder rather
        # than a resource picker.
        return []

    async def provision_node(
        self,
        *,
        pool_id: str,
        gpu_type: Optional[str] = None,
        gpu_count: int = 1,
        vcpu: int = 4,
        ram_gb: int = 16,
        region: Optional[str] = None,
        credentials: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        # Workers register themselves; the orchestrator should never reach
        # this path for a worker pool. Return a placeholder so any caller
        # that does invoke it can fail gracefully.
        raise NotImplementedError(
            "Worker pools self-provision. Use the Workers tab in the dashboard "
            "(or POST /v1/admin/workers/tokens) to mint a bootstrap token and "
            "register a host via inferia-worker."
        )

    async def wait_for_ready(self, *, provider_instance_id: str, **_kwargs: Any) -> bool:
        return True

    async def deprovision_node(self, *, provider_instance_id: str, **_kwargs: Any) -> None:
        # Revoking a worker happens through worker_controller's revoke
        # path (DELETE /v1/admin/workers/{node_id}). This is a no-op fallback.
        return None

    async def get_logs(
        self,
        *,
        provider_instance_id: str,
        lines: int = 100,
        **_kwargs: Any,
    ) -> List[str]:
        return []

    async def get_log_streaming_info(self, *, provider_instance_id: str, **_kwargs: Any) -> Dict[str, Any]:
        return {"streaming_supported": False}

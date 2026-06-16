"""Adapter registry.

Maps a provider string (the value of compute_pools.provider) to the
adapter class that handles its provisioning lifecycle. Cloud providers
(AWS / GCP / Azure) are served by Pulumi-Automation-API-backed adapters;
DePIN and self-hosted providers have their own adapters.

Lambda Cloud and Runpod were previously served by SkyPilot. Pulumi has
no first-class providers for them, so they are intentionally absent
from this registry until someone writes Pulumi
`dynamic.ResourceProvider`s wrapping their REST APIs.
"""
import logging
from typing import Optional

from orchestration.provisioning.engine.base import AdapterType
from providers.akash.akash_adapter import (
    AkashAdapter,
)
from providers.k8s.k8s_adapter import (
    KubernetesAdapter,
)
from providers.nosana.nosana_adapter import (
    NosanaAdapter,
)
from providers.pulumi.pulumi_aws_adapter import (
    PulumiAWSAdapter,
)
from providers.pulumi.pulumi_azure_adapter import (
    PulumiAzureAdapter,
)
from providers.pulumi.pulumi_gcp_adapter import (
    PulumiGCPAdapter,
)
from providers.worker.worker_adapter import (
    WorkerAdapter,
)

logger = logging.getLogger(__name__)

ADAPTER_REGISTRY = {
    "nosana": NosanaAdapter,
    "k8s": KubernetesAdapter,
    "akash": AkashAdapter,
    # Self-hosted (inferia-worker) topology. 'on_prem' is the DB enum
    # value used in compute_pools.provider; both keys are accepted so
    # the createpool flow doesn't reject worker-pool requests.
    "worker": WorkerAdapter,
    "on_prem": WorkerAdapter,
    # Cloud providers via Pulumi Automation API.
    "aws": PulumiAWSAdapter,
    "gcp": PulumiGCPAdapter,
    "azure": PulumiAzureAdapter,
}

# Keys that are aliases for another canonical provider — hidden from
# /inventory/providers so the dashboard doesn't render duplicate cards.
_ADAPTER_ALIASES = {"on_prem"}


def get_adapter(provider: str):
    """Return an adapter instance for the given provider string.

    Raises ValueError when the provider isn't registered. Cloud providers
    (aws/gcp/azure) are constructed with no args — they read settings
    (pulumi_state_dir etc) at __init__ time from
    orchestration.config.settings.
    """
    cls = ADAPTER_REGISTRY.get(provider)
    if not cls:
        raise ValueError(
            f"No adapter registered for provider '{provider}'. "
            f"Available: {sorted(set(ADAPTER_REGISTRY) - _ADAPTER_ALIASES)}"
        )
    return cls()


def is_direct_provision_provider(provider: Optional[str]) -> bool:
    """Does deleting a node of ``provider`` need an inline ``adapter.
    deprovision_node`` call to stop an external (paid) job?

    True iff the provider has a registered adapter whose CAPABILITIES set
    ``supports_direct_provisioning`` AND whose ``ADAPTER_TYPE`` is NOT
    ``CLOUD`` — i.e. the gate is ``supports_direct_provisioning AND
    ADAPTER_TYPE != CLOUD``. Cloud providers (aws/gcp/azure) tear their
    EC2/VM down via the Pulumi reconciler (``force_cancel`` → CancelHandler),
    so the delete handler must NOT also call their adapter. DePIN providers
    (nosana/akash) record a ``provider_instance_id`` (e.g. the Nosana job
    address) that keeps billing until the adapter stops it — so their delete
    path MUST call ``deprovision_node`` inline.

    ON_PREM is NOT a blanket False: the KubernetesAdapter is ``ON_PREM`` yet
    sets ``supports_direct_provisioning=True``, so k8s DOES return True (its
    pod/job must be torn down inline, the same as a DePIN job). It is only
    the WorkerAdapter that returns False — it is ``ON_PREM`` with
    ``supports_direct_provisioning=False`` (there is no external job to
    stop). The classification is driven entirely by the two capability/type
    attributes, not by the adapter family name.

    Reads CLASS attributes only — it must NOT instantiate the adapter
    (``KubernetesAdapter()`` loads kubeconfig and raises; cloud adapters
    read pulumi settings at __init__). Unknown / falsy providers → False.
    """
    if not provider:
        return False
    cls = ADAPTER_REGISTRY.get(provider)
    if cls is None:
        return False
    caps = getattr(cls, "CAPABILITIES", None)
    if caps is None or not getattr(caps, "supports_direct_provisioning", False):
        return False
    return getattr(cls, "ADAPTER_TYPE", None) != AdapterType.CLOUD


async def _deprovision_direct_node(
    node_row: dict,
    *,
    pool_credential_name: Optional[str] = None,
) -> tuple[bool, Optional[str]]:
    """Stop the external DePIN job a node row points at, before its inventory
    row is marked terminated.

    Reads ``provider`` + ``provider_instance_id`` from ``node_row`` and calls
    ``get_adapter(provider).deprovision_node(...)`` with the pool's credential
    name. This is a fast sidecar call for Nosana, so callers ``await`` it
    inline (NOT the 202/async pulumi-destroy pattern).

    Returns:
        ``(True, None)``  — deprovision succeeded, OR was skipped because the
                node never provisioned an external job (no
                ``provider_instance_id`` or a ``placeholder:`` sentinel). The
                caller may safely mark the node terminated.
        ``(False, err)`` — ``deprovision_node`` raised. ``err`` is
                ``str(exc)`` for the caller to record as
                ``metadata.deprovision_error``. An ERROR is also logged
                flagging a POSSIBLE LEAKED external job (provider + instance
                id) so ops can see it; the caller should stamp a
                ``deprovision_failed`` marker (with ``err``) but STILL mark
                the node terminated (a stuck external job is visible via the
                marker + error rather than wedging the row forever).
    """
    provider = node_row.get("provider")
    pii = node_row.get("provider_instance_id")
    if not pii or str(pii).startswith("placeholder:"):
        logger.info(
            "_deprovision_direct_node: skipping deprovision for provider=%s "
            "(no external job: provider_instance_id=%r)",
            provider, pii,
        )
        return True, None
    try:
        adapter = get_adapter(provider)
        await adapter.deprovision_node(
            provider_instance_id=pii,
            provider_credential_name=pool_credential_name,
        )
        return True, None
    except Exception as exc:
        logger.error(
            "_deprovision_direct_node: deprovision_node FAILED for provider=%s "
            "provider_instance_id=%s — POSSIBLE LEAKED external job still "
            "running and billing; ops must verify/stop it manually",
            provider, pii, exc_info=True,
        )
        return False, str(exc)


def get_registered_providers() -> list:
    return list(ADAPTER_REGISTRY.keys())


def get_provider_info() -> dict:
    info = {}
    for name, cls in ADAPTER_REGISTRY.items():
        if name in _ADAPTER_ALIASES:
            continue
        info[name] = {
            "adapter_type": cls.ADAPTER_TYPE,
            "capabilities": cls.CAPABILITIES.to_dict(),
        }
    return info

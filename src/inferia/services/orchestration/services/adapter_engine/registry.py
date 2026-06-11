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

from inferia.services.orchestration.services.adapter_engine.adapters.akash.akash_adapter import (
    AkashAdapter,
)
from inferia.services.orchestration.services.adapter_engine.adapters.k8s.k8s_adapter import (
    KubernetesAdapter,
)
from inferia.services.orchestration.services.adapter_engine.adapters.nosana.nosana_adapter import (
    NosanaAdapter,
)
from inferia.services.orchestration.services.adapter_engine.adapters.pulumi.pulumi_aws_adapter import (
    PulumiAWSAdapter,
)
from inferia.services.orchestration.services.adapter_engine.adapters.pulumi.pulumi_azure_adapter import (
    PulumiAzureAdapter,
)
from inferia.services.orchestration.services.adapter_engine.adapters.pulumi.pulumi_gcp_adapter import (
    PulumiGCPAdapter,
)
from inferia.services.orchestration.services.adapter_engine.adapters.worker.worker_adapter import (
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
    inferia.services.orchestration.config.settings.
    """
    cls = ADAPTER_REGISTRY.get(provider)
    if not cls:
        raise ValueError(
            f"No adapter registered for provider '{provider}'. "
            f"Available: {sorted(set(ADAPTER_REGISTRY) - _ADAPTER_ALIASES)}"
        )
    return cls()


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

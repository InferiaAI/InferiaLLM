import importlib.util
import logging

from inferia.services.orchestration.services.adapter_engine.adapters.nosana.nosana_adapter import (
    NosanaAdapter,
)
from inferia.services.orchestration.services.adapter_engine.adapters.k8s.k8s_adapter import (
    KubernetesAdapter,
)
from inferia.services.orchestration.services.adapter_engine.adapters.akash.akash_adapter import (
    AkashAdapter,
)
from inferia.services.orchestration.services.adapter_engine.adapters.worker.worker_adapter import (
    WorkerAdapter,
)
from inferia.services.orchestration.services.adapter_engine.adapters.aws.aws_adapter import (
    AWSAdapter,
)

logger = logging.getLogger(__name__)

ADAPTER_REGISTRY = {
    "nosana": NosanaAdapter,
    "k8s": KubernetesAdapter,
    "akash": AkashAdapter,
    # The 'worker' provider is the dashboard-facing name for the self-hosted
    # (inferia-worker) deployment topology. 'on_prem' is the DB enum value
    # used in compute_pools.provider; both are accepted here so the
    # createpool flow doesn't reject worker-pool requests.
    "worker": WorkerAdapter,
    "on_prem": WorkerAdapter,
    # Native AWS EC2 adapter (boto3-based, no SkyPilot dependency).
    "aws": AWSAdapter,
}

# Keys that are aliases for another canonical provider — hidden from
# /inventory/providers so the dashboard doesn't render duplicate cards.
_ADAPTER_ALIASES = {"on_prem"}

# AWS is now served by the native AWSAdapter above (boto3 + cloud-init),
# so it is intentionally NOT in the SkyPilot list.
_SKYPILOT_PROVIDERS = ("gcp", "azure", "lambda", "runpod")
_skypilot_available = importlib.util.find_spec("sky") is not None

# Register SkyPilot-based cloud adapters only when the 'sky' package is installed
if _skypilot_available:
    from inferia.services.orchestration.services.adapter_engine.adapters.skypilot.skypilot_adapter import (
        SkyPilotAdapter,
    )

    for _provider in _SKYPILOT_PROVIDERS:
        ADAPTER_REGISTRY[_provider] = SkyPilotAdapter
else:
    logger.warning(
        "SkyPilot is not installed — cloud provider adapters (gcp, azure, lambda, runpod) "
        "are unavailable. Install with: pip install 'skypilot[gcp]'. "
        "AWS is served by the native boto3 adapter and does not need SkyPilot."
    )


def get_adapter(provider: str):
    """
    Get adapter instance for a provider.

    Args:
        provider: Provider name (e.g., "nosana", "akash", "k8s")

    Returns:
        ProviderAdapter instance

    Raises:
        ValueError: If provider is not registered
    """
    if not _skypilot_available and provider in _SKYPILOT_PROVIDERS:
        raise ValueError(
            f"Provider '{provider}' requires SkyPilot which is not installed. "
            f"Install with: pip install 'skypilot[{provider}]'"
        )
    adapter_cls = ADAPTER_REGISTRY.get(provider)
    if not adapter_cls:
        raise ValueError(
            f"No adapter registered for provider '{provider}'. "
            f"Available providers: {list(ADAPTER_REGISTRY.keys())}"
        )
    if _skypilot_available and adapter_cls is SkyPilotAdapter:
        return adapter_cls(cloud=provider)
    return adapter_cls()


def get_registered_providers() -> list:
    """
    Get list of all registered provider names.

    Returns:
        List of provider name strings
    """
    return list(ADAPTER_REGISTRY.keys())


def get_provider_info() -> dict:
    """
    Get information about all registered providers including their capabilities.

    Returns:
        Dict mapping provider names to their capabilities
    """
    info = {}
    for provider_name, adapter_cls in ADAPTER_REGISTRY.items():
        if provider_name in _ADAPTER_ALIASES:
            continue
        info[provider_name] = {
            "adapter_type": adapter_cls.ADAPTER_TYPE,
            "capabilities": adapter_cls.CAPABILITIES.to_dict(),
        }
    return info

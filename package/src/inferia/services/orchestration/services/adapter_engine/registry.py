# from services.adapter_engine.adapters.aws.aws_adapter import AWSAdapter
from inferia.services.orchestration.services.adapter_engine.adapters.nosana.nosana_adapter import (
    NosanaAdapter,
)
from inferia.services.orchestration.services.adapter_engine.adapters.k8s.k8s_adapter import (
    KubernetesAdapter,
)
from inferia.services.orchestration.services.adapter_engine.adapters.skypilot.skypilot_adapter import (
    SkyPilotAdapter,
)
from inferia.services.orchestration.services.adapter_engine.adapters.akash.akash_adapter import (
    AkashAdapter,
)

ADAPTER_REGISTRY = {
    "aws": SkyPilotAdapter,
    "nosana": NosanaAdapter,
    "k8s": KubernetesAdapter,
    "skypilot": SkyPilotAdapter,
    "akash": AkashAdapter,
}


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
    adapter_cls = ADAPTER_REGISTRY.get(provider)
    if not adapter_cls:
        raise ValueError(
            f"No adapter registered for provider '{provider}'. "
            f"Available providers: {list(ADAPTER_REGISTRY.keys())}"
        )
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
        info[provider_name] = {
            "adapter_type": adapter_cls.ADAPTER_TYPE,
            "capabilities": adapter_cls.CAPABILITIES.to_dict(),
        }
    return info

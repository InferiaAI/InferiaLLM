# from services.adapter_engine.adapters.aws.aws_adapter import AWSAdapter
from inferia.services.orchestration.services.adapter_engine.adapters.nosana.nosana_adapter import NosanaAdapter
from inferia.services.orchestration.services.adapter_engine.adapters.k8s.k8s_adapter import KubernetesAdapter
from inferia.services.orchestration.services.adapter_engine.adapters.skypilot.skypilot_adapter import SkyPilotAdapter

ADAPTER_REGISTRY = {
    "aws": SkyPilotAdapter,
    "nosana": NosanaAdapter,
    "k8s": KubernetesAdapter,
    "skypilot": SkyPilotAdapter,
}

def get_adapter(provider: str):
    adapter_cls = ADAPTER_REGISTRY.get(provider)
    if not adapter_cls:
        raise ValueError(f"No adapter registered for {provider}")
    return adapter_cls()

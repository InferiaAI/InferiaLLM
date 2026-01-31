import random
import logging
from typing import Dict, List, Optional
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

@dataclass
class ServiceEndpoint:
    url: str
    is_canary: bool = False

@dataclass
class ServiceRoute:
    name: str
    endpoints: List[ServiceEndpoint] = field(default_factory=list)
    canary_weight: int = 0 

class TrafficRouter:
    def __init__(self):
        self.routes: Dict[str, ServiceRoute] = {}
        # Dummy data initialization
        self._init_dummy_data()

    def _init_dummy_data(self):
        """Initialize with some dummy data for testing."""
        # Standard service
        self.register_service("inference-engine", ["http://vllm-primary:8000"])
        
        # Service with a canary
        self.register_service("text-processor", ["http://text-proc-v1:8000"])
        # Manually adding a canary endpoint for simulation
        if "text_processor" in self.routes:
             self.routes["text-processor"].endpoints.append(
                 ServiceEndpoint(url="http://text-proc-v2-canary:8000", is_canary=True)
             )

    def register_service(self, service_name: str, endpoints: List[str]):
        """Register a new service with default (stable) endpoints."""
        service_endpoints = [ServiceEndpoint(url=url, is_canary=False) for url in endpoints]
        self.routes[service_name] = ServiceRoute(name=service_name, endpoints=service_endpoints)
        logger.info(f"Registered service: {service_name} with endpoints: {endpoints}")

    def add_canary_endpoint(self, service_name: str, endpoint_url: str):
        """Add a canary endpoint to an existing service."""
        if service_name in self.routes:
            self.routes[service_name].endpoints.append(
                ServiceEndpoint(url=endpoint_url, is_canary=True)
            )
            logger.info(f"Added canary endpoint {endpoint_url} to {service_name}")

    def update_weights(self, service_name: str, canary_weight: int):
        """Update traffic split for a service."""
        if service_name not in self.routes:
            logger.warning(f"Service {service_name} not found.")
            return

        if not (0 <= canary_weight <= 100):
            raise ValueError("Canary weight must be between 0 and 100")

        self.routes[service_name].canary_weight = canary_weight
        logger.info(f"Updated canary weight for {service_name} to {canary_weight}%")

    def get_route(self, service_name: str) -> Optional[str]:
        """Get the target endpoint based on traffic weights."""
        route = self.routes.get(service_name)
        if not route:
            return None

        canary_endpoints = [ep for ep in route.endpoints if ep.is_canary]
        stable_endpoints = [ep for ep in route.endpoints if not ep.is_canary]

        # If no stable endpoints, try to fallback to canary (or return None)
        if not stable_endpoints:
             if canary_endpoints:
                 return random.choice(canary_endpoints).url
             return None

        # Logic:
        # If canary exists and weight > 0, check chance
        if canary_endpoints and route.canary_weight > 0:
            if random.randint(1, 100) <= route.canary_weight:
                return random.choice(canary_endpoints).url

        # Default to stable
        return random.choice(stable_endpoints).url

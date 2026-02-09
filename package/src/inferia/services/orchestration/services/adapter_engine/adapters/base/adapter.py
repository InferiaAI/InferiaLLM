from abc import ABC, abstractmethod
from typing import List, Dict


class ProviderAdapter(ABC):
    """
    Base interface for all cloud / provider adapters.
    """

    @abstractmethod
    async def discover_nodes(self) -> List[Dict]:
        """
        Discover nodes that belong to this provider.
        Should NOT fetch live usage.
        """
        pass

    @abstractmethod
    async def get_node_metadata(self, node_id: str) -> Dict:
        """
        Provider-specific metadata (region, AZ, instance type, GPU type).
        """
        pass

    @abstractmethod
    async def reconcile(self) -> None:
        """
        Periodic reconciliation loop:
        - add missing nodes
        - mark terminated nodes
        """
        pass

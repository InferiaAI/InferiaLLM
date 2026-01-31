from abc import ABC, abstractmethod
from typing import Dict, List, Optional


class ProviderAdapter(ABC):
    """
    Strict provider adapter contract.
    Orchestration layer depends ONLY on this interface.
    """

    ADAPTER_TYPE = "cloud"  # cloud | depin | on_prem

    # -------------------------------------------------
    # DISCOVERY
    # -------------------------------------------------
    @abstractmethod
    async def discover_resources(self) -> List[Dict]:
        """
        Returns normalized provider resources suitable for provider_resources table.
        """
        raise NotImplementedError

    # -------------------------------------------------
    # PROVISION
    # -------------------------------------------------
    @abstractmethod
    async def provision_node(
        self,
        *,
        provider_resource_id: str,
        pool_id: str,
        region: Optional[str] = None,
        use_spot: bool = False,
        metadata: Optional[Dict] = None,
    ) -> Dict:
        """
        Provision a single compute node.
        Must return inventory-compatible fields.
        """
        raise NotImplementedError

    @abstractmethod
    async def wait_for_ready(self, *, provider_instance_id: str, timeout: int = 300) -> str:
        """
        Wait until the node is ready and return its access endpoint (e.g. URL).
        If the provider doesn't require waiting, it should return the endpoint immediately.
        """
        raise NotImplementedError

    # -------------------------------------------------
    # DEPROVISION
    # -------------------------------------------------
    @abstractmethod
    async def deprovision_node(
        self,
        *,
        provider_instance_id: str
    ) -> None:
        raise NotImplementedError

    # -------------------------------------------------
    # LOGS
    # -------------------------------------------------
    @abstractmethod
    async def get_logs(self, *, provider_instance_id: str) -> Dict:
        """
        Fetch logs for a specific instance.
        Returns a dict containing 'logs': List[str] or List[Dict] usually.
        """
        raise NotImplementedError

    @abstractmethod
    async def get_log_streaming_info(self, *, provider_instance_id: str) -> Dict:
        """
        Returns connection details for WebSocket log streaming.
        """
        raise NotImplementedError

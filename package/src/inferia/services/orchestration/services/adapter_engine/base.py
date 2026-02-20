from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any
from enum import Enum


class AdapterType(str, Enum):
    """Standardized adapter type classification."""

    CLOUD = "cloud"
    DEPIN = "depin"
    ON_PREM = "on_prem"


class PricingModel(str, Enum):
    """Standardized pricing model classification."""

    FIXED = "fixed"
    SPOT = "spot"
    AUCTION = "auction"
    ON_DEMAND = "on_demand"


@dataclass
class ProviderCapabilities:
    """
    Standardized capabilities for compute providers.
    Used by orchestration layer for provider-agnostic decision making.
    """

    # Core features
    supports_log_streaming: bool = False
    supports_confidential_compute: bool = False
    supports_spot_instances: bool = False
    supports_multi_gpu: bool = True

    # Lifecycle
    is_ephemeral: bool = False  # Provider manages node lifecycle (DePIN, spot)
    requires_readiness_poll: bool = True  # Needs wait_for_ready call
    readiness_timeout_seconds: int = 300
    polling_interval_seconds: int = 20

    # Integration
    requires_sidecar: bool = False
    supports_direct_provisioning: bool = True

    # Pricing
    pricing_model: PricingModel = PricingModel.FIXED

    # Metadata
    features: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert capabilities to dictionary for serialization."""
        return {
            "supports_log_streaming": self.supports_log_streaming,
            "supports_confidential_compute": self.supports_confidential_compute,
            "supports_spot_instances": self.supports_spot_instances,
            "supports_multi_gpu": self.supports_multi_gpu,
            "is_ephemeral": self.is_ephemeral,
            "requires_readiness_poll": self.requires_readiness_poll,
            "readiness_timeout_seconds": self.readiness_timeout_seconds,
            "polling_interval_seconds": self.polling_interval_seconds,
            "requires_sidecar": self.requires_sidecar,
            "supports_direct_provisioning": self.supports_direct_provisioning,
            "pricing_model": self.pricing_model.value,
            "features": self.features,
        }


class ProviderAdapter(ABC):
    """
    Strict provider adapter contract.
    Orchestration layer depends ONLY on this interface.

    All implementations must:
    1. Define ADAPTER_TYPE class attribute
    2. Define CAPABILITIES class attribute
    3. Implement all abstract methods
    """

    ADAPTER_TYPE: AdapterType = AdapterType.CLOUD
    CAPABILITIES: ProviderCapabilities = ProviderCapabilities()

    @classmethod
    def get_capabilities(cls) -> ProviderCapabilities:
        """Return provider capabilities. Override for dynamic capabilities."""
        return cls.CAPABILITIES

    # -------------------------------------------------
    # DISCOVERY
    # -------------------------------------------------
    @abstractmethod
    async def discover_resources(self) -> List[Dict]:
        """
        Returns normalized provider resources suitable for provider_resources table.

        Standard resource format:
        {
            "provider": str,
            "provider_resource_id": str,
            "gpu_type": Optional[str],
            "gpu_count": int,
            "gpu_memory_gb": Optional[int],
            "vcpu": int,
            "ram_gb": int,
            "region": str,
            "pricing_model": str,
            "price_per_hour": float,
            "metadata": Dict,
        }
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
        provider_credential_name: Optional[str] = None,
    ) -> Dict:
        """
        Provision a single compute node.
        Must return inventory-compatible fields.

        Args:
            provider_resource_id: Provider-specific resource identifier
            pool_id: Pool ID for this deployment
            region: Optional region constraint
            use_spot: Whether to use spot instances
            metadata: Additional metadata for the deployment
            provider_credential_name: Which named credential to use for this provider

        Standard return format:
        {
            "provider": str,
            "provider_instance_id": str,
            "hostname": str,
            "gpu_total": int,
            "vcpu_total": int,
            "ram_gb_total": int,
            "region": str,
            "node_class": str,
            "metadata": Dict,
            "expose_url": Optional[str],  # Direct URL if available
        }
        """
        raise NotImplementedError

    @abstractmethod
    async def wait_for_ready(
        self,
        *,
        provider_instance_id: str,
        timeout: int = 300,
        provider_credential_name: Optional[str] = None,
    ) -> str:
        """
        Wait until the node is ready and return its access endpoint (e.g. URL).
        If the provider doesn't require waiting, it should return the endpoint immediately.

        Returns:
            str: Access endpoint URL or readiness indicator
        """
        raise NotImplementedError

    # -------------------------------------------------
    # DEPROVISION
    # -------------------------------------------------
    @abstractmethod
    async def deprovision_node(
        self,
        *,
        provider_instance_id: str,
        provider_credential_name: Optional[str] = None,
    ) -> None:
        """Deprovision and clean up the compute node."""
        raise NotImplementedError

    # -------------------------------------------------
    # LOGS
    # -------------------------------------------------
    @abstractmethod
    async def get_logs(
        self,
        *,
        provider_instance_id: str,
        provider_credential_name: Optional[str] = None,
    ) -> Dict:
        """
        Fetch logs for a specific instance.

        Returns:
            Dict containing 'logs': List[str] or List[Dict]
        """
        raise NotImplementedError

    @abstractmethod
    async def get_log_streaming_info(
        self,
        *,
        provider_instance_id: str,
        provider_credential_name: Optional[str] = None,
    ) -> Dict:
        """
        Returns connection details for WebSocket log streaming.

        Returns:
            Dict with WebSocket connection details or empty dict if not supported
        """
        raise NotImplementedError

    # -------------------------------------------------
    # UTILITY
    # -------------------------------------------------
    def is_ephemeral(self) -> bool:
        """
        Check if this provider uses ephemeral compute.
        Ephemeral providers manage node lifecycle externally (DePIN, spot instances).
        """
        return self.get_capabilities().is_ephemeral

    def get_readiness_timeout(self) -> int:
        """Get the recommended readiness timeout in seconds."""
        return self.get_capabilities().readiness_timeout_seconds

    def get_polling_interval(self) -> int:
        """Get the recommended polling interval in seconds."""
        return self.get_capabilities().polling_interval_seconds

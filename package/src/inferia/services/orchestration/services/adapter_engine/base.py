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
    supports_cluster_mode: bool = (
        False  # Persistent cluster (SkyPilot) vs job-based (Nosana)
    )

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
            "supports_cluster_mode": self.supports_cluster_mode,
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
    # CLUSTER MODE (Optional - for SkyPilot-style persistent clusters)
    # -------------------------------------------------
    async def provision_cluster(
        self,
        *,
        cluster_name: str,
        gpu_type: str,
        region: Optional[str] = None,
        use_spot: bool = False,
        provider_credential_name: Optional[str] = None,
    ) -> Dict:
        """
        Provision a persistent cluster (for cluster-based providers like SkyPilot).

        This is called when creating a pool, not when starting a deployment.
        The cluster persists until the pool is deleted.

        Args:
            cluster_name: Unique name for the cluster
            gpu_type: GPU type (A100, A10G, etc.)
            region: Cloud region
            use_spot: Use spot instances
            provider_credential_name: Named credential to use

        Returns:
            Dict with cluster info including cluster_id, hostname, etc.
        """
        raise NotImplementedError("Cluster mode not supported")

    async def terminate_cluster(
        self,
        *,
        cluster_id: str,
        provider_credential_name: Optional[str] = None,
    ) -> None:
        """
        Terminate a persistent cluster (for cluster-based providers like SkyPilot).

        This is called when deleting a pool.
        """
        raise NotImplementedError("Cluster mode not supported")

    async def deploy_service(
        self,
        *,
        cluster_id: str,
        service_name: str,
        image: str,
        ports: List[Dict],
        env: Optional[Dict] = None,
        cmd: Optional[List[str]] = None,
        provider_credential_name: Optional[str] = None,
    ) -> str:
        """
        Deploy a service on an existing cluster (for cluster-based providers).

        Called when starting a deployment on a cluster-based pool.

        Args:
            cluster_id: The cluster to deploy to
            service_name: Unique name for this service
            image: Docker image to run
            ports: List of ports to expose [{"port": 9000, "type": "http"}]
            env: Environment variables
            cmd: Command to run
            provider_credential_name: Named credential

        Returns:
            Service URL/endpoint
        """
        raise NotImplementedError("Cluster mode not supported")

    async def stop_service(
        self,
        *,
        cluster_id: str,
        service_name: str,
        provider_credential_name: Optional[str] = None,
    ) -> None:
        """
        Stop a service on a cluster (for cluster-based providers).

        Called when stopping a deployment. The cluster remains alive.
        """
        raise NotImplementedError("Cluster mode not supported")

    async def get_cluster_status(
        self,
        *,
        cluster_id: str,
        provider_credential_name: Optional[str] = None,
    ) -> Dict:
        """
        Get the status of a cluster.

        Returns:
            Dict with cluster state, resources, etc.
        """
        raise NotImplementedError("Cluster mode not supported")

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

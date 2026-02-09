from kubernetes import client, config
from inferia.services.orchestration.services.adapter_engine.base import ProviderAdapter
from typing import List, Dict, Optional
import uuid
import logging

logger = logging.getLogger(__name__)


class KubernetesAdapter(ProviderAdapter):
    """
    Kubernetes Adapter
    - Discovery: node capacity
    - Provisioning: create pod
    - Deprovisioning: delete pod
    """

    ADAPTER_TYPE = "on_prem"

    def __init__(self):
        # Load ~/.kube/config or in-cluster config
        try:
            config.load_kube_config()
        except Exception:
            config.load_incluster_config()

        self.core = client.CoreV1Api()

    # -----------------------------------------------------
    # DISCOVER RESOURCES
    # -----------------------------------------------------
    async def discover_resources(self) -> List[Dict]:
        """
        Discover available node capacity.
        """
        try:
            nodes = self.core.list_node().items

            resources = []

            for node in nodes:
                capacity = node.status.capacity

                cpu = int(capacity.get("cpu", "0"))
                memory_str = capacity.get("memory", "0")
                # Handle different memory formats (Ki, Mi, Gi)
                if "Ki" in memory_str:
                    memory = int(memory_str.replace("Ki", "")) // (1024 * 1024)
                elif "Mi" in memory_str:
                    memory = int(memory_str.replace("Mi", "")) // 1024
                elif "Gi" in memory_str:
                    memory = int(memory_str.replace("Gi", ""))
                else:
                    memory = int(memory_str) // (1024 * 1024 * 1024)

                gpu = 0
                gpu_type = None
                for k, v in capacity.items():
                    if "gpu" in k.lower():
                        gpu = int(v)
                        gpu_type = "nvidia" if "nvidia" in k.lower() else "gpu"

                resources.append({
                    "provider": "k8s",
                    "provider_resource_id": f"k8s-node-{node.metadata.name}",
                    "gpu_type": gpu_type,
                    "gpu_count": gpu,
                    "gpu_memory_gb": None,
                    "vcpu": cpu,
                    "ram_gb": memory,
                    "region": "local",
                    "pricing_model": "fixed",
                    "price_per_hour": 0.0,
                    "metadata": {
                        "node": node.metadata.name,
                    }
                })

            return resources

        except Exception:
            logger.exception("Kubernetes resource discovery error")
            return []

    # -----------------------------------------------------
    # PROVISION NODE (CREATE POD)
    # -----------------------------------------------------
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
        Provision a compute node by creating a Kubernetes pod.
        """
        pod_name = f"inferia-worker-{uuid.uuid4().hex[:6]}"
        namespace = (metadata or {}).get("namespace", "default")
        image = (metadata or {}).get("image", "busybox")
        cmd = (metadata or {}).get("cmd", ["sleep", "3600"])

        # Extract resource requirements from metadata
        gpu_allocated = (metadata or {}).get("gpu_allocated", 0)
        vcpu_allocated = (metadata or {}).get("vcpu_allocated", 1)
        ram_gb_allocated = (metadata or {}).get("ram_gb_allocated", 1)

        # Build resource requests
        resource_requests = {
            "cpu": str(vcpu_allocated),
            "memory": f"{ram_gb_allocated}Gi",
        }
        resource_limits = dict(resource_requests)

        if gpu_allocated > 0:
            resource_limits["nvidia.com/gpu"] = str(gpu_allocated)

        pod = client.V1Pod(
            metadata=client.V1ObjectMeta(
                name=pod_name,
                labels={
                    "inferia": "worker",
                    "pool_id": str(pool_id),
                }
            ),
            spec=client.V1PodSpec(
                restart_policy="Never",
                containers=[
                    client.V1Container(
                        name="worker",
                        image=image,
                        command=cmd if isinstance(cmd, list) else [cmd],
                        resources=client.V1ResourceRequirements(
                            requests=resource_requests,
                            limits=resource_limits,
                        )
                    )
                ]
            )
        )

        self.core.create_namespaced_pod(
            namespace=namespace,
            body=pod
        )

        return {
            "provider": "k8s",
            "provider_instance_id": pod_name,
            "instance_type": provider_resource_id,
            "hostname": pod_name,
            "gpu_total": gpu_allocated,
            "vcpu_total": vcpu_allocated,
            "ram_gb_total": ram_gb_allocated,
            "node_class": "fixed",
            "metadata": {
                "namespace": namespace,
                "pool_id": str(pool_id),
                "image": image,
            }
        }

    # -----------------------------------------------------
    # DEPROVISION NODE
    # -----------------------------------------------------
    async def deprovision_node(self, *, provider_instance_id: str) -> None:
        """
        Deprovision a compute node by deleting the Kubernetes pod.
        """
        try:
            # Try to get pod metadata to find namespace
            # Default to "default" namespace if not found
            namespace = "default"
            try:
                pods = self.core.list_pod_for_all_namespaces(
                    field_selector=f"metadata.name={provider_instance_id}"
                )
                if pods.items:
                    namespace = pods.items[0].metadata.namespace
            except Exception:
                pass

            self.core.delete_namespaced_pod(
                name=provider_instance_id,
                namespace=namespace
            )
        except Exception:
            logger.exception("Kubernetes deprovision error")
            raise

    # -----------------------------------------------------
    # LOGS
    # -----------------------------------------------------
    async def get_logs(self, *, provider_instance_id: str) -> Dict:
        """
        Fetch logs from a Kubernetes pod.
        """
        try:
            # Similar to deprovision, we need to find the namespace
            namespace = "default"
            try:
                pods = self.core.list_pod_for_all_namespaces(
                    field_selector=f"metadata.name={provider_instance_id}"
                )
                if pods.items:
                    namespace = pods.items[0].metadata.namespace
            except Exception:
                pass

            logs = self.core.read_namespaced_pod_log(
                name=provider_instance_id,
                namespace=namespace,
                tail_lines=100
            )
            return {"logs": logs.split("\n")}
        except Exception as e:
            logger.exception("Kubernetes get_logs error")
            return {"logs": [f"Error fetching logs: {str(e)}"]}

    async def get_log_streaming_info(self, *, provider_instance_id: str) -> Dict:
        """
        Returns info for K8s log streaming.
        """
        # Standardize for future K8s WS logs
        return {
            "ws_url": None,
            "provider": "k8s",
            "subscription": {
                "pod_name": provider_instance_id
            }
        }

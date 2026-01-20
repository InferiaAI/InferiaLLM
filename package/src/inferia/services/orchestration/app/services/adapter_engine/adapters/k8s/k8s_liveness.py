import asyncio
import logging
from kubernetes import client, config

logger = logging.getLogger("k8s-liveness")


class KubernetesLivenessSource:
    """
    Watches Kubernetes pods and emits heartbeats
    into Inventory Manager.
    """

    def __init__(self, inventory_repo, namespace="default"):
        try:
            config.load_kube_config()
        except Exception:
            config.load_incluster_config()

        self.core = client.CoreV1Api()
        self.inventory_repo = inventory_repo
        self.namespace = namespace
        

    async def run(self, interval_seconds=15):
        logger.info("Starting Kubernetes liveness loop")

        while True:
            try:
                pods = self.core.list_namespaced_pod(
                    namespace=self.namespace,
                    label_selector="inferia=worker"
                ).items

                for pod in pods:
                    pod_name = pod.metadata.name

                    # Ensure inventory row exists
                    await self.inventory_repo.ensure_node_exists({
                        "pool_id": pod.metadata.labels.get("pool_id"),
                        "provider": "k8s",
                        "provider_instance_id": pod_name,
                        "hostname": pod_name,
                        "gpu_total": 0,
                        "vcpu_total": 1,
                        "ram_gb_total": 1,
                    })

                    # Heartbeat → transition to READY
                    if pod.status.phase == "Running":
                        await self.inventory_repo.heartbeat({
                            "provider": "k8s",
                            "provider_instance_id": pod_name,
                            "gpu_allocated": 0,
                            "vcpu_allocated": 0,
                            "ram_gb_allocated": 0,
                            "health_score": 100,
                        })

            except Exception as e:
                logger.error("K8s liveness error: %s", e)

            await asyncio.sleep(interval_seconds)

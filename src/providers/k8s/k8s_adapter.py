from kubernetes import client, config
from orchestration.provisioning.engine.base import (
    ProviderAdapter,
    AdapterType,
    PricingModel,
    ProviderCapabilities,
)
from typing import List, Dict, Optional
import asyncio
import functools
import os
import shlex
import uuid
import logging
import time


async def _run_sync(func, *args, **kwargs):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, functools.partial(func, *args, **kwargs))

logger = logging.getLogger(__name__)

# Port each engine listens on, with metadata["port"] winning. Ports are
# per-provider today; the Nosana job builder hardcodes 11434 the same way.
_ENGINE_PORTS = {
    "ollama": 11434,
    "vllm": 8000,
    "vllm-omni": 8000,
}
_DEFAULT_ENGINE_PORT = 8000

# Where each engine stores downloaded weights. Without a volume here the model
# lives in the container's writable layer, so every replaced pod downloads it
# again and self-healing takes as long as the original pull.
_ENGINE_MODEL_DIRS = {
    "ollama": "/root/.ollama",
    "vllm": "/root/.cache/huggingface",
    "vllm-omni": "/root/.cache/huggingface",
}
_DEFAULT_CACHE_SIZE = "50Gi"
_MODEL_VOLUME_NAME = "models"

# Ollama drops a model from memory 5 minutes after the last request, so the
# next one reloads from disk: 14.9s cold against 0.34s resident on qwen2:0.5b.
_DEFAULT_KEEP_ALIVE = "-1"


def _ollama_startup(model_id: str, keep_alive: str):
    """Command, args, env and readiness probe for an Ollama container.

    Ollama opens its port with no model loaded, so starting the image alone
    gives a container that passes a port check and answers "model not found".
    The model has to be pulled, and the server must already be running for the
    pull to work. Readiness therefore checks the model is present rather than
    the port being open, so RUNNING means the deployment can serve.

    Engines that take the model as a start argument (vLLM) need none of this.
    """
    safe = shlex.quote(model_id)
    script = (
        "ollama serve & "
        "until ollama list >/dev/null 2>&1; do sleep 1; done; "
        f"ollama pull {safe}; "
        "wait"
    )
    readiness = client.V1Probe(
        _exec=client.V1ExecAction(
            command=["/bin/sh", "-c", f"ollama show {safe} >/dev/null 2>&1"],
        ),
        initial_delay_seconds=5,
        period_seconds=10,
        failure_threshold=90,  # a model pull can be slow
    )
    env = [client.V1EnvVar(name="OLLAMA_KEEP_ALIVE", value=keep_alive)]
    return ["/bin/sh", "-c"], [script], env, readiness


def _engine_port(metadata: Optional[Dict]) -> int:
    md = metadata or {}
    if md.get("port"):
        return int(md["port"])
    engine = str(md.get("engine") or "").lower()
    port = _ENGINE_PORTS.get(engine)
    if port is None:
        logger.warning(
            "k8s: no known port for engine %r, defaulting to %d",
            engine, _DEFAULT_ENGINE_PORT,
        )
        return _DEFAULT_ENGINE_PORT
    return port


class KubernetesAdapter(ProviderAdapter):
    """
    Kubernetes Adapter
    - Discovery: node capacity
    - Provisioning: create pod
    - Deprovisioning: delete pod
    """

    ADAPTER_TYPE = AdapterType.ON_PREM

    CAPABILITIES = ProviderCapabilities(
        supports_log_streaming=False,  # Native K8s log streaming
        supports_confidential_compute=False,
        supports_spot_instances=False,
        supports_multi_gpu=True,
        is_ephemeral=False,  # On-prem nodes are managed by cluster admin
        requires_readiness_poll=True,
        readiness_timeout_seconds=900,  # a first engine image pull is GBs
        polling_interval_seconds=5,
        requires_sidecar=False,
        supports_direct_provisioning=True,
        pricing_model=PricingModel.FIXED,  # On-prem typically has fixed costs
        features={
            "native_k8s": True,
            "pod_based": True,
            "persistent_volumes": True,
            "namespace_isolation": True,
        },
    )

    def __init__(self):
        # Load ~/.kube/config or in-cluster config
        try:
            config.load_kube_config()
        except Exception:
            config.load_incluster_config()

        self.core = client.CoreV1Api()
        self.apps = client.AppsV1Api()

    # -----------------------------------------------------
    # DISCOVER RESOURCES
    # -----------------------------------------------------
    async def discover_resources(self) -> List[Dict]:
        """
        Discover available node capacity.
        """
        try:
            nodes = (await _run_sync(self.core.list_node)).items

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

                resources.append(
                    {
                        "provider": "k8s",
                        "provider_resource_id": f"k8s-node-{node.metadata.name}",
                        "gpu_type": gpu_type,
                        "gpu_count": gpu,
                        "gpu_memory_gb": None,
                        "vcpu": cpu,
                        "ram_gb": memory,
                        "region": "local",
                        "pricing_model": self.CAPABILITIES.pricing_model.value,
                        "price_per_hour": 0.0,
                        "metadata": {
                            "node": node.metadata.name,
                        },
                    }
                )

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
        provider_credential_name: Optional[str] = None,
    ) -> Dict:
        """
        Provision a compute node by creating a Kubernetes pod.
        """
        pod_name = f"inferia-worker-{uuid.uuid4().hex[:6]}"
        namespace = (metadata or {}).get("namespace", "default")
        image = (metadata or {}).get("image", "busybox")
        # Kubernetes "command" REPLACES the image entrypoint, so "cmd" maps to
        # args instead: ollama/ollama is ENTRYPOINT /bin/ollama + CMD ["serve"],
        # and passing ["serve"] as command execs a binary named "serve".
        def _as_list(v):
            if v is None:
                return None
            return v if isinstance(v, list) else [v]

        args = _as_list((metadata or {}).get("cmd"))
        command = _as_list((metadata or {}).get("command"))

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

        port = _engine_port(metadata)
        labels = {
            "inferia": "worker",
            "pool_id": str(pool_id),
            # Nothing addresses a pod by name; everything selects on this.
            "inferia-instance": pod_name,
        }

        # TCP rather than an HTTP path: engines disagree on where their health
        # endpoint lives, and an open socket is what the router needs.
        readiness = client.V1Probe(
            tcp_socket=client.V1TCPSocketAction(port=port),
            initial_delay_seconds=10,
            period_seconds=10,
            failure_threshold=60,
        )
        _engine = str((metadata or {}).get("engine") or "").lower()
        _model = (metadata or {}).get("model_id")
        env = None
        if _engine == "ollama" and _model:
            keep_alive = str(
                (metadata or {}).get("keep_alive") or _DEFAULT_KEEP_ALIVE
            )
            command, args, env, readiness = _ollama_startup(_model, keep_alive)

        container = client.V1Container(
            name="worker",
            image=image,
            command=command,
            args=args,
            env=env,
            ports=[client.V1ContainerPort(container_port=port, name="http")],
            resources=client.V1ResourceRequirements(
                requests=resource_requests,
                limits=resource_limits,
            ),
            readiness_probe=readiness,
            liveness_probe=client.V1Probe(
                tcp_socket=client.V1TCPSocketAction(port=port),
                initial_delay_seconds=120,
                period_seconds=20,
                failure_threshold=3,
            ),
        )

        # A template, not one shared claim: ReadWriteOnce binds to a single
        # node, so replicas placed anywhere else could not start.
        model_dir = _ENGINE_MODEL_DIRS.get(_engine)
        claim_templates = None
        if model_dir:
            size = str((metadata or {}).get("model_cache_size") or _DEFAULT_CACHE_SIZE)
            claim_templates = [
                client.V1PersistentVolumeClaim(
                    metadata=client.V1ObjectMeta(
                        name=_MODEL_VOLUME_NAME, labels=labels,
                    ),
                    spec=client.V1PersistentVolumeClaimSpec(
                        access_modes=["ReadWriteOnce"],
                        resources=client.V1VolumeResourceRequirements(
                            requests={"storage": size},
                        ),
                    ),
                )
            ]
            container.volume_mounts = [
                client.V1VolumeMount(
                    name=_MODEL_VOLUME_NAME, mount_path=model_dir,
                )
            ]

        # A StatefulSet only because a Deployment has no volumeClaimTemplates.
        workload = client.V1StatefulSet(
            metadata=client.V1ObjectMeta(name=pod_name, labels=labels),
            spec=client.V1StatefulSetSpec(
                replicas=1,
                service_name=pod_name,
                selector=client.V1LabelSelector(
                    match_labels={"inferia-instance": pod_name},
                ),
                template=client.V1PodTemplateSpec(
                    metadata=client.V1ObjectMeta(labels=labels),
                    spec=client.V1PodSpec(containers=[container]),
                ),
                volume_claim_templates=claim_templates,
                # The default waits for each ordinal in turn, which here
                # would serialise one model load per replica.
                pod_management_policy="Parallel",
            ),
        )

        # A cluster-internal address only resolves for a control plane inside
        # the cluster, so this is an operator setting rather than a per-model
        # one. metadata still wins for a one-off.
        service_type = str(
            (metadata or {}).get("service_type")
            or os.environ.get("K8S_SERVICE_TYPE")
            or "ClusterIP"
        )
        service = client.V1Service(
            metadata=client.V1ObjectMeta(name=pod_name, labels=labels),
            spec=client.V1ServiceSpec(
                selector={"inferia-instance": pod_name},
                ports=[client.V1ServicePort(port=port, target_port=port, name="http")],
                type=service_type,
            ),
        )

        # First, so a failure below rolls back an empty Service rather than
        # a running workload.
        await _run_sync(
            self.core.create_namespaced_service,
            namespace=namespace, body=service,
        )

        try:
            await _run_sync(
                self.apps.create_namespaced_stateful_set,
                namespace=namespace, body=workload,
            )
        except Exception:
            logger.exception(
                "k8s: statefulset create failed for %s, rolling back", pod_name,
            )
            await self._delete_quietly(
                self.core.delete_namespaced_service, pod_name, namespace,
            )
            raise

        return {
            "provider": "k8s",
            "provider_instance_id": pod_name,
            "instance_type": provider_resource_id,
            "hostname": pod_name,
            "expose_url": await self._resolve_url(pod_name, namespace),
            "gpu_total": gpu_allocated,
            "vcpu_total": vcpu_allocated,
            "ram_gb_total": ram_gb_allocated,
            "node_class": "fixed",
            "metadata": {
                "namespace": namespace,
                "pool_id": str(pool_id),
                "image": image,
                "port": port,
            },
        }

    # -----------------------------------------------------
    # HELPERS
    # -----------------------------------------------------
    async def _resolve_url(self, name: str, namespace: str) -> str:
        """The address to reach this workload on, read back from the Service.

        ClusterIP gives the in-cluster DNS name. NodePort gives a node address
        and the allocated port, which is what an out-of-cluster control plane
        can actually reach.
        """
        svc = await _run_sync(
            self.core.read_namespaced_service, name=name, namespace=namespace,
        )
        spec_port = svc.spec.ports[0]

        if svc.spec.type != "NodePort":
            return f"http://{name}.{namespace}.svc.cluster.local:{spec_port.port}"

        node_port = spec_port.node_port
        nodes = await _run_sync(self.core.list_node)
        for node in nodes.items:
            for addr in node.status.addresses or []:
                if addr.type in ("InternalIP", "Hostname"):
                    return f"http://{addr.address}:{node_port}"
        raise RuntimeError(f"no node address found for NodePort service {name}")

    async def _delete_quietly(self, fn, name: str, namespace: str) -> None:
        """Delete and swallow a 404. Used on rollback and deprovision, where a
        missing object is the outcome we wanted."""
        try:
            await _run_sync(fn, name=name, namespace=namespace)
        except client.exceptions.ApiException as e:
            if e.status != 404:
                raise

    async def _pod_name_for(self, provider_instance_id: str, namespace: str):
        """Resolve a pod behind the workload. Selected by label rather than
        by name, so it still returns one once there is more than one replica."""
        pods = await _run_sync(
            self.core.list_namespaced_pod,
            namespace=namespace,
            label_selector=f"inferia-instance={provider_instance_id}",
        )
        if not pods.items:
            return None
        return pods.items[0].metadata.name

    # -----------------------------------------------------
    # WAIT FOR READY
    # -----------------------------------------------------
    async def wait_for_ready(
        self,
        *,
        provider_instance_id: str,
        timeout: int = 120,
        provider_credential_name: Optional[str] = None,
    ) -> str:
        """Wait until the StatefulSet reports a ready replica.

        Returns the Service address, which is what the router connects to.
        Readiness comes from the container probe, so a replica counted here has
        an open port rather than merely a scheduled pod.
        """
        import asyncio

        capabilities = self.get_capabilities()
        start = time.time()
        poll_interval = capabilities.polling_interval_seconds
        namespace = "default"

        while True:
            try:
                sts = await _run_sync(
                    self.apps.read_namespaced_stateful_set,
                    name=provider_instance_id, namespace=namespace,
                )

                if (sts.status.ready_replicas or 0) >= 1:
                    return await self._resolve_url(
                        provider_instance_id, namespace,
                    )

            except client.exceptions.ApiException as e:
                if e.status == 404:
                    logger.warning(
                        "k8s: statefulset %s not found yet, waiting",
                        provider_instance_id,
                    )
                else:
                    raise
            except Exception as e:
                logger.warning("k8s: error checking statefulset status: %s", e)

            if time.time() - start > timeout:
                raise RuntimeError(
                    f"StatefulSet {provider_instance_id} had no ready replica "
                    f"within {timeout}s"
                )

            await asyncio.sleep(poll_interval)

    # -----------------------------------------------------
    # DEPROVISION NODE
    # -----------------------------------------------------
    async def deprovision_node(
        self,
        *,
        provider_instance_id: str,
        provider_credential_name: Optional[str] = None,
    ) -> None:
        """Delete the StatefulSet, its Service and every model volume it made.

        All are removed even if one is already gone, so a partially created
        node leaves nothing behind.
        """
        namespace = "default"
        try:
            await self._delete_quietly(
                self.apps.delete_namespaced_stateful_set,
                provider_instance_id, namespace,
            )
            await self._delete_quietly(
                self.core.delete_namespaced_service,
                provider_instance_id, namespace,
            )
            # Deleting a StatefulSet leaves its claims behind by design, and
            # their names carry a replica ordinal, so they go by label.
            await _run_sync(
                self.core.delete_collection_namespaced_persistent_volume_claim,
                namespace=namespace,
                label_selector=f"inferia-instance={provider_instance_id}",
            )
        except Exception:
            logger.exception("Kubernetes deprovision error")
            raise

    # -----------------------------------------------------
    # LOGS
    # -----------------------------------------------------
    async def get_logs(
        self,
        *,
        provider_instance_id: str,
        provider_credential_name: Optional[str] = None,
    ) -> Dict:
        """Fetch logs from the pod behind the StatefulSet.

        provider_instance_id names the StatefulSet, not a pod, so the pod is
        resolved through its label.
        """
        namespace = "default"
        try:
            pod_name = await self._pod_name_for(provider_instance_id, namespace)
            if not pod_name:
                return {"logs": [f"No pod yet for {provider_instance_id}"]}

            logs = await _run_sync(
                self.core.read_namespaced_pod_log,
                name=pod_name, namespace=namespace, tail_lines=100,
            )
            return {"logs": logs.split("\n")}
        except Exception as e:
            logger.exception("Kubernetes get_logs error")
            return {"logs": [f"Error fetching logs: {str(e)}"]}

    async def get_log_streaming_info(
        self,
        *,
        provider_instance_id: str,
        provider_credential_name: Optional[str] = None,
    ) -> Dict:
    # -----------------------------------------------------
        """
        Returns info for K8s log streaming.
        TODO: Implement WebSocket streaming via K8s API.
        """
        # Standardize for future K8s WS logs
        return {
            "ws_url": None,
            "provider": "k8s",
            "subscription": {"pod_name": provider_instance_id},
            "supported": False,
            "message": "Native K8s log streaming via WebSocket not yet implemented",
        }

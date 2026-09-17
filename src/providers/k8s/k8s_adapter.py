from kubernetes import client, config
from orchestration import recipes
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

_DEFAULT_CACHE_SIZE = "50Gi"
_MODEL_VOLUME_NAME = "models"

_KEDA_GROUP = "keda.sh"
_KEDA_VERSION = "v1alpha1"
_KEDA_PLURAL = "scaledobjects"

_DEFAULT_MAX_REPLICAS = 3
_DEFAULT_IN_FLIGHT_PER_REPLICA = 3
# A gate, not a goal: no scaling happens until P95 is past it.
_DEFAULT_P95_TARGET_SECONDS = 10
_DEFAULT_MIN_SAMPLES = 2
_METRIC_WINDOW = "2m"
# Overrides a Kubernetes default of 300s, which holds replicas long after
# the load that caused them has gone.
_SCALE_DOWN_STABILISATION_SECONDS = 30

# Ollama drops a model from memory 5 minutes after the last request, so the
# next one reloads from disk: 14.9s cold against 0.34s resident on qwen2:0.5b.
_DEFAULT_KEEP_ALIVE = "-1"


def _ollama_startup(model_id: str, keep_alive: str):
    """Command, args, env and readiness probe for an Ollama container.

    Ollama opens its port with no model loaded, so starting the image alone
    gives a container that passes a port check and answers "model not found".
    The model has to be pulled, and the server must already be running for the
    pull to work. Whether the model arrived is checked by the startup probe,
    not by readiness - see _probes.

    Engines that take the model as a start argument (vLLM) need none of this.
    """
    safe = shlex.quote(model_id)
    script = (
        "ollama serve & "
        "until ollama list >/dev/null 2>&1; do sleep 1; done; "
        f"ollama pull {safe}; "
        "wait"
    )
    env = [client.V1EnvVar(name="OLLAMA_KEEP_ALIVE", value=keep_alive)]
    return ["/bin/sh", "-c"], [script], env


def _vllm_args(model_id: str, port: int, max_model_len=None):
    """Args only: the image's entrypoint is already `vllm serve`."""
    args = [
        model_id,
        "--served-model-name", model_id,
        "--host", "0.0.0.0",
        "--port", str(port),
    ]
    if max_model_len:
        args += ["--max-model-len", str(max_model_len)]
    return args


def _scaler_query(deployment_id: str, p95_target: int, min_samples: int) -> str:
    """The Prometheus query KEDA scales on.

    Three terms multiplied, not three triggers: KEDA takes the MAXIMUM across
    triggers, so a second trigger could only raise the replica count and never
    hold it down. A guard has to gate the value inside one query.

    In-flight rather than a queue-depth metric because the engine reports no
    queue of its own. Everything counted here is either generating or waiting
    inside the engine, so it is where the queue is observable.
    """
    d = deployment_id
    return (
        f'sum(inferia_upstream_in_flight_requests{{deployment="{d}"}})'
        " * (scalar(histogram_quantile(0.95, sum by (le) (rate("
        f'inferia_inference_duration_seconds_bucket{{deployment="{d}"}}'
        f"[{_METRIC_WINDOW}])))) > bool {p95_target})"
        f' * (scalar(sum(increase(inferia_inference_requests_total{{deployment="{d}"}}'
        f"[{_METRIC_WINDOW}]))) > bool {min_samples})"
    )


def _scaled_object_body(name: str, deployment_id: str, metadata: Optional[Dict]) -> Dict:
    """A KEDA ScaledObject targeting the engine's StatefulSet."""
    md = metadata or {}
    max_replicas = int(md.get("autoscale_max_replicas") or _DEFAULT_MAX_REPLICAS)
    per_replica = int(
        md.get("autoscale_in_flight_per_replica") or _DEFAULT_IN_FLIGHT_PER_REPLICA
    )
    p95_target = int(md.get("autoscale_p95_seconds") or _DEFAULT_P95_TARGET_SECONDS)
    min_samples = int(md.get("autoscale_min_samples") or _DEFAULT_MIN_SAMPLES)
    prom = str(
        md.get("prometheus_address")
        or os.environ.get("KEDA_PROMETHEUS_ADDRESS")
        or "http://prometheus.monitoring.svc.cluster.local:9090"
    )

    return {
        "apiVersion": f"{_KEDA_GROUP}/{_KEDA_VERSION}",
        "kind": "ScaledObject",
        "metadata": {"name": name, "labels": {"inferia-instance": name}},
        "spec": {
            "scaleTargetRef": {"kind": "StatefulSet", "name": name},
            "minReplicaCount": 1,
            "maxReplicaCount": max_replicas,
            "advanced": {
                "horizontalPodAutoscalerConfig": {
                    "behavior": {
                        "scaleDown": {
                            "stabilizationWindowSeconds": (
                                _SCALE_DOWN_STABILISATION_SECONDS
                            ),
                        },
                        "scaleUp": {"stabilizationWindowSeconds": 0},
                    },
                },
            },
            "triggers": [
                {
                    "type": "prometheus",
                    "metadata": {
                        "serverAddress": prom,
                        # Per replica, not total: the HPA divides the query
                        # result by the current replica count.
                        "threshold": str(per_replica),
                        "query": _scaler_query(
                            deployment_id, p95_target, min_samples,
                        ),
                    },
                }
            ],
        },
    }


def _probes(
    engine: str, port: int, model_id: Optional[str],
    health_path: Optional[str],
):
    """Startup, readiness and liveness probes for one engine.

    Three, because they answer different questions and want different budgets.

    Startup asks whether the model is loaded. It gets 15 minutes, and
    Kubernetes runs neither of the others until it passes, so the slow check
    cannot interfere with the fast one.

    Readiness asks only whether the process accepts connections. It must not
    mean "is idle": a replica added under load that answers "busy" is removed
    from the Service for being under load, which is what the autoscaler just
    added it to handle. Backpressure belongs to the router, not the kubelet.

    Liveness restarts the pod, and a restart costs a full model load, so it is
    the most forgiving of the three.

    Every probe sets timeout_seconds. The default is 1, which an exec probe
    cannot meet on a busy node: the kubelet spawns a process through the
    container runtime, and that overhead sits on top of the command itself.
    """
    if health_path:
        def _action():
            return {
                "http_get": client.V1HTTPGetAction(path=health_path, port=port),
            }
    else:
        def _action():
            return {"tcp_socket": client.V1TCPSocketAction(port=port)}

    # Ollama opens its port before the model exists, so a port check would
    # pass with nothing served. Nothing else tells us the model arrived.
    if engine == "ollama" and model_id:
        safe = shlex.quote(model_id)
        startup = client.V1Probe(
            _exec=client.V1ExecAction(
                command=["/bin/sh", "-c", f"ollama show {safe} >/dev/null 2>&1"],
            ),
            period_seconds=10,
            timeout_seconds=5,
            failure_threshold=90,
        )
    else:
        startup = client.V1Probe(
            period_seconds=10, timeout_seconds=5, failure_threshold=90,
            **_action(),
        )

    readiness = client.V1Probe(
        period_seconds=10, timeout_seconds=5, failure_threshold=3, **_action(),
    )
    liveness = client.V1Probe(
        period_seconds=30, timeout_seconds=10, failure_threshold=5, **_action(),
    )
    return startup, readiness, liveness


def _engine_port(metadata: Optional[Dict]) -> int:
    """The port to expose, with deployment configuration winning."""
    md = metadata or {}
    if md.get("port"):
        return int(md["port"])
    return _recipe_for(md).port


def _recipe_for(metadata: Optional[Dict]) -> recipes.ResolvedRecipe:
    md = metadata or {}
    return recipes.resolve(
        md.get("engine"), recipes.profile_for(md.get("gpu_allocated", 0)),
    )


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
        self.custom = client.CustomObjectsApi()

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
        recipe = _recipe_for(metadata)
        image = (metadata or {}).get("image") or recipe.image or "busybox"
        ram_gb_allocated = (
            (metadata or {}).get("ram_gb_allocated") or recipe.memory_gb or 1
        )

        # Two values, deliberately. Kubernetes takes a quantity string, which
        # can be fractional ("500m"); compute_inventory.vcpu_total is a whole
        # number of cores. Conflating them writes "500m" into an int column.
        vcpu_allocated = (metadata or {}).get("vcpu_allocated") or 1
        cpu_request = str(
            (metadata or {}).get("vcpu_allocated") or recipe.cpu_request or "500m"
        )
        memory = f"{ram_gb_allocated}Gi"

        resource_requests = {"cpu": cpu_request, "memory": memory}

        # Memory keeps its ceiling: an OOM kill is contained, a node running
        # out of memory is not. CPU gets one only where the CPU is the
        # accelerator - see the recipe file for why.
        resource_limits = {"memory": memory}
        if not recipe.cpu_burstable:
            resource_limits["cpu"] = cpu_request

        if gpu_allocated > 0:
            resource_limits["nvidia.com/gpu"] = str(gpu_allocated)

        port = _engine_port(metadata)
        labels = {
            "inferia": "worker",
            "pool_id": str(pool_id),
            # Nothing addresses a pod by name; everything selects on this.
            "inferia-instance": pod_name,
        }

        _engine = str((metadata or {}).get("engine") or "").lower()
        _model = (metadata or {}).get("model_id")
        env_values = dict(recipe.env)
        if _engine == "ollama" and _model:
            keep_alive = str(
                (metadata or {}).get("keep_alive") or _DEFAULT_KEEP_ALIVE
            )
            command, args, extra = _ollama_startup(_model, keep_alive)
            env_values.update({e.name: e.value for e in extra})
        elif _engine == "vllm" and _model and args is None and command is None:
            args = _vllm_args(_model, port, (metadata or {}).get("max_model_len"))

        # Last, so the deployment's values win.
        config_env = (metadata or {}).get("env")
        if isinstance(config_env, dict):
            env_values.update({str(k): str(v) for k, v in config_env.items()})
        env = [
            client.V1EnvVar(name=k, value=v) for k, v in sorted(env_values.items())
        ]

        startup, readiness, liveness = _probes(
            _engine, port, _model, recipe.health_path,
        )

        container = client.V1Container(
            name="worker",
            image=image,
            # Explicit, because Kubernetes otherwise infers it from the tag:
            # a `latest` tag means Always, and Always fails the container when
            # the registry cannot be reached even if the image is on the node.
            image_pull_policy=recipe.image_pull_policy,
            command=command,
            args=args,
            env=env or None,
            ports=[client.V1ContainerPort(container_port=port, name="http")],
            resources=client.V1ResourceRequirements(
                requests=resource_requests,
                limits=resource_limits,
            ),
            startup_probe=startup,
            readiness_probe=readiness,
            liveness_probe=liveness,
        )

        # A template, not one shared claim: ReadWriteOnce binds to a single
        # node, so replicas placed anywhere else could not start.
        model_dir = recipe.model_dir
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

        if (metadata or {}).get("autoscale"):
            await self._create_scaled_object(pod_name, namespace, metadata)

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

    async def _create_scaled_object(
        self, name: str, namespace: str, metadata: Optional[Dict],
    ) -> None:
        """Hand the workload to KEDA.

        Failure is logged, not raised: a cluster without KEDA installed should
        still get a working deployment, just one that does not scale itself.
        """
        deployment_id = str((metadata or {}).get("deployment_id") or name)
        try:
            await _run_sync(
                self.custom.create_namespaced_custom_object,
                group=_KEDA_GROUP,
                version=_KEDA_VERSION,
                namespace=namespace,
                plural=_KEDA_PLURAL,
                body=_scaled_object_body(name, deployment_id, metadata),
            )
        except Exception:
            logger.exception(
                "k8s: could not create a ScaledObject for %s; the deployment "
                "will run without autoscaling", name,
            )

    async def _delete_scaled_object(self, name: str, namespace: str) -> None:
        """Remove it whether or not one was created.

        A ScaledObject left behind keeps KEDA reconciling a workload that no
        longer exists, and deprovision cannot tell whether autoscaling was on.
        """
        try:
            await _run_sync(
                self.custom.delete_namespaced_custom_object,
                group=_KEDA_GROUP,
                version=_KEDA_VERSION,
                namespace=namespace,
                plural=_KEDA_PLURAL,
                name=name,
            )
        except client.exceptions.ApiException as e:
            # 404 is the usual case: autoscaling was off, or KEDA is absent.
            if e.status != 404:
                raise
        except Exception:
            logger.exception("k8s: could not delete the ScaledObject for %s", name)

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
        """Delete the ScaledObject, StatefulSet, Service and model volumes.

        All are removed even if one is already gone, so a partially created
        node leaves nothing behind.
        """
        namespace = "default"
        try:
            # Before the workload: KEDA writes to a StatefulSet it still owns.
            await self._delete_scaled_object(provider_instance_id, namespace)
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

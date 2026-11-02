"""Kubernetes objects for one deployment on the llm-d data plane.

Pure functions over plain dicts (the kubernetes client accepts dicts as bodies),
so the whole shape is unit-testable without a cluster.

One InferiaLLM deployment becomes, in namespace ``K8S_LLMD_NAMESPACE``:

  <name>-ms            Deployment  model servers; publish KV-cache events over ZMQ
  <name>-ms-fallback   Service     headless; Envoy's fail-open target if the EPP is down
  <name>               InferencePool  GIE v1: selects the model pods, names the EPP
  <name>-epp-config    ConfigMap   EPP plugin config (precise prefix-cache routing)
  <name>-envoy         ConfigMap   Envoy sidecar config (vendored from the chart)
  <name>-epp           Deployment  llm-d router: Envoy sidecar + EPP
  <name>-epp           Service     ClusterIP: ext_proc (9002) + EPP metrics (9090)
  <name>               Service     the gateway: Envoy's listener (8081) only. This is
                                   the endpoint InferiaLLM routes to; its type follows
                                   K8S_SERVICE_TYPE like the adapter's other Services.

Shapes follow llm-d-router-standalone v1.0.0 and the llm-d v1.0.0
precise-prefix-cache-routing guide (sources: deploy/kind/README.md). Two
deliberate differences from the chart:
  * the Envoy ConfigMap is named per deployment (the chart's fixed name "envoy"
    collides between two deployments in one namespace);
  * no RBAC objects: the EPP runs as a shared ServiceAccount created once per
    namespace by deploy/kind/manifests/llmd-namespace-bootstrap.yaml.
"""
from __future__ import annotations

import copy
import os
import re
import uuid
from dataclasses import dataclass
from typing import Dict, List, Optional

import yaml

from providers.k8s.llmd import _chart_envoy

LLMD_ID_PREFIX = "inferia-llmd-"
INSTANCE_LABEL = "inferia-llmd-instance"
POOL_POD_LABEL = "inferia-llmd-pool"
ROUTER_POD_LABEL = "inferia-llmd-router"
MANAGED_BY = {"app.kubernetes.io/managed-by": "inferiallm"}

DATAPLANE_KEY = "dataplane"
DATAPLANE_LLMD = "llm-d"

DEFAULT_NAMESPACE = "inferia-llmd"
EPP_SERVICE_ACCOUNT = "inferia-llmd-epp"
EPP_CONFIG_FILE = "inferia-llmd-plugins.yaml"

SIM_ENGINE = "llm-d-inference-sim"
SUPPORTED_ENGINES = frozenset({SIM_ENGINE})

# Pinned by multi-arch index digest as well as tag.
SIM_IMAGE = (
    "ghcr.io/llm-d/llm-d-inference-sim:v1.0.0"
    "@sha256:1908c421cbb44130a3212934a0dd1e279dacfe3480be93f4c28b45cd29b93ec8"
)
EPP_IMAGE = (
    "ghcr.io/llm-d/llm-d-router-endpoint-picker:v1.0.0"
    "@sha256:024188e998f516879f68532016ca24fabca00afbdcc2af9a61b17634e197df2c"
)
ENVOY_IMAGE = (
    "docker.io/envoyproxy/envoy:distroless-v1.33.2"
    "@sha256:85500e28ed088ec39ff0adc1be3d358a8ad062926aaa62c36b28bde00919e4e8"
)

MODEL_PORT = 8000
KV_EVENTS_PORT = 5556
KV_REPLAY_PORT = 5559
# Must equal the model servers' --block-size.
BLOCK_SIZE_TOKENS = 64
EPP_GRPC_PORT = 9002
EPP_HEALTH_PORT = 9003
EPP_METRICS_PORT = 9090
PROXY_PORT = 8081
PROXY_READY_PORT = 19001

MAX_REPLICAS = 32
_SERVICE_TYPES = frozenset({"ClusterIP", "NodePort", "LoadBalancer"})
_DNS_LABEL = re.compile(r"^[a-z0-9]([-a-z0-9]{0,61}[a-z0-9])?$")
_LABEL_VALUE = re.compile(r"^([A-Za-z0-9]([-A-Za-z0-9_.]{0,61}[A-Za-z0-9])?)?$")
# A model id goes into container args and the EPP config; keep it to what HF
# repo ids and simulator names use.
_MODEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/:-]{0,199}$")


class LlmdSpecError(ValueError):
    """The deployment cannot run on the llm-d data plane as configured."""


def new_instance_name() -> str:
    return f"{LLMD_ID_PREFIX}{uuid.uuid4().hex[:6]}"


def is_llmd_instance(provider_instance_id: Optional[str]) -> bool:
    return bool(provider_instance_id) and str(provider_instance_id).startswith(LLMD_ID_PREFIX)


def wants_llmd(metadata: Optional[Dict]) -> bool:
    return str((metadata or {}).get(DATAPLANE_KEY) or "").lower() == DATAPLANE_LLMD


def llmd_namespace() -> str:
    """The namespace llm-d deployments live in.

    An operator setting, like the adapter's K8S_SERVICE_TYPE, because delete and
    readiness receive only the instance id. Its RBAC comes from the bootstrap.
    """
    ns = os.environ.get("K8S_LLMD_NAMESPACE") or DEFAULT_NAMESPACE
    if not _DNS_LABEL.match(ns):
        raise LlmdSpecError(f"K8S_LLMD_NAMESPACE={ns!r} is not a valid namespace name")
    return ns


# --- names -------------------------------------------------------------------
def gateway_service_name(name: str) -> str:
    return name


def model_server_name(name: str) -> str:
    return f"{name}-ms"


def fallback_service_name(name: str) -> str:
    return f"{name}-ms-fallback"


def epp_name(name: str) -> str:
    return f"{name}-epp"


def epp_service_name(name: str) -> str:
    return f"{name}-epp"


def epp_config_name(name: str) -> str:
    return f"{name}-epp-config"


def envoy_config_name(name: str) -> str:
    return f"{name}-envoy"


# --- spec --------------------------------------------------------------------
@dataclass(frozen=True)
class LlmdSpec:
    name: str
    namespace: str
    engine: str
    model: str
    replicas: int
    service_type: str
    pool_id: Optional[str] = None
    deployment_id: Optional[str] = None


def spec_from_metadata(
    *, name: str, namespace: str, metadata: Optional[Dict], pool_id: Optional[str],
) -> LlmdSpec:
    """Validate a deployment's metadata for the llm-d data plane.

    Refuses rather than defaults: a deployment that cannot run here must fail
    with a reason, not silently run on another data plane (interfaces §5).
    """
    md = metadata or {}
    if not name.startswith(LLMD_ID_PREFIX) or not _DNS_LABEL.match(name):
        raise LlmdSpecError(f"bad llm-d instance name {name!r}")
    if not _DNS_LABEL.match(namespace):
        raise LlmdSpecError(f"bad namespace {namespace!r}")

    engine = str(md.get("engine") or SIM_ENGINE).lower()
    if engine not in SUPPORTED_ENGINES:
        raise LlmdSpecError(
            f"engine {engine!r} is not supported on the llm-d data plane yet "
            f"(supported: {sorted(SUPPORTED_ENGINES)}). Precise prefix routing "
            "tokenizes through the model servers' /v1/*/render endpoints, which "
            "the pinned vLLM recipe is not verified to serve."
        )
    if md.get("kv_cache") is not None:
        raise LlmdSpecError(
            "kv_cache offload is not supported by the llm-d inference simulator; "
            "use the direct vLLM deployment path after GPU validation"
        )

    model = md.get("model_id")
    if not isinstance(model, str) or not _MODEL.match(model):
        raise LlmdSpecError(f"bad model id {model!r}")

    raw_replicas = md.get("replicas", 1)
    if isinstance(raw_replicas, bool) or not isinstance(raw_replicas, int):
        raise LlmdSpecError(f"replicas must be an integer, got {raw_replicas!r}")
    if not 1 <= raw_replicas <= MAX_REPLICAS:
        raise LlmdSpecError(f"replicas must be 1..{MAX_REPLICAS}, got {raw_replicas}")

    service_type = str(
        md.get("service_type") or os.environ.get("K8S_SERVICE_TYPE") or "ClusterIP"
    )
    if service_type not in _SERVICE_TYPES:
        raise LlmdSpecError(f"bad service_type {service_type!r}")

    def _label_or_none(v):
        s = None if v is None else str(v)
        return s if s and _LABEL_VALUE.match(s) else None

    return LlmdSpec(
        name=name,
        namespace=namespace,
        engine=engine,
        model=model,
        replicas=raw_replicas,
        service_type=service_type,
        pool_id=_label_or_none(pool_id),
        deployment_id=_label_or_none(md.get("deployment_id")),
    )


# --- objects -----------------------------------------------------------------
def _labels(spec: LlmdSpec) -> Dict[str, str]:
    labels = {INSTANCE_LABEL: spec.name, **MANAGED_BY}
    if spec.deployment_id:
        labels["inferia-deployment"] = spec.deployment_id
    if spec.pool_id:
        labels["pool_id"] = spec.pool_id
    return labels


def _meta(spec: LlmdSpec, name: str) -> Dict:
    return {"name": name, "namespace": spec.namespace, "labels": _labels(spec)}


def _model_pod_labels(spec: LlmdSpec) -> Dict[str, str]:
    # inferia-instance lets the adapter's existing pod and log helpers find a
    # model server by instance id; only model pods carry it.
    return {POOL_POD_LABEL: spec.name, "inferia-instance": spec.name, INSTANCE_LABEL: spec.name}


def _field_env(name: str, path: str) -> Dict:
    return {"name": name, "valueFrom": {"fieldRef": {"fieldPath": path}}}


def _sim_args(spec: LlmdSpec) -> List[str]:
    """llm-d-inference-sim v1.0.0 flags (docs/configuration.md, docs/kv-cache.md).

    Booleans are written bare: v1.0.0 refuses ``--flag true``.
    """
    return [
        f"--model={spec.model}",
        f"--port={MODEL_PORT}",
        "--max-num-seqs=16",
        "--max-model-len=16384",
        "--enable-kvcache",
        "--kv-cache-size=2048",
        f"--block-size={BLOCK_SIZE_TOKENS}",
        f"--zmq-endpoint=tcp://*:{KV_EVENTS_PORT}",
        f"--kv-events-replay-endpoint=tcp://*:{KV_REPLAY_PORT}",
        "--use-vllm-map-event-format",
        "--latency-calculator=per-token",
        "--prefill-overhead=5ms",
        "--prefill-time-per-token=200us",
        "--inter-token-latency=5ms",
    ]


def model_server_deployment(spec: LlmdSpec) -> Dict:
    pod_labels = _model_pod_labels(spec)
    probe = {"httpGet": {"path": "/health", "port": MODEL_PORT}}
    return {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": _meta(spec, model_server_name(spec.name)),
        "spec": {
            "replicas": spec.replicas,
            "selector": {"matchLabels": {POOL_POD_LABEL: spec.name}},
            "template": {
                "metadata": {"labels": pod_labels},
                "spec": {
                    "automountServiceAccountToken": False,
                    "securityContext": {
                        "runAsNonRoot": True,
                        "runAsUser": 65532,
                        "seccompProfile": {"type": "RuntimeDefault"},
                    },
                    "containers": [{
                        "name": "model-server",
                        "image": SIM_IMAGE,
                        "imagePullPolicy": "IfNotPresent",
                        "args": _sim_args(spec),
                        "env": [
                            _field_env("POD_NAME", "metadata.name"),
                            _field_env("POD_NAMESPACE", "metadata.namespace"),
                            # Builds the KV-event topic kv@<ip>:<port>@<model>.
                            _field_env("POD_IP", "status.podIP"),
                        ],
                        "ports": [
                            {"name": "http", "containerPort": MODEL_PORT},
                            {"name": "kv-events", "containerPort": KV_EVENTS_PORT},
                            {"name": "kv-replay", "containerPort": KV_REPLAY_PORT},
                        ],
                        "readinessProbe": {**probe, "periodSeconds": 2},
                        "livenessProbe": {**probe, "periodSeconds": 10},
                        "resources": {
                            "requests": {"cpu": "100m", "memory": "64Mi"},
                            "limits": {"memory": "512Mi"},
                        },
                        "securityContext": {
                            "allowPrivilegeEscalation": False,
                            "readOnlyRootFilesystem": True,
                            "capabilities": {"drop": ["ALL"]},
                        },
                    }],
                },
            },
        },
    }


def fallback_service(spec: LlmdSpec) -> Dict:
    return {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": _meta(spec, fallback_service_name(spec.name)),
        "spec": {
            "clusterIP": "None",
            "selector": {POOL_POD_LABEL: spec.name},
            "ports": [{"name": "http", "port": MODEL_PORT, "targetPort": MODEL_PORT, "protocol": "TCP"}],
        },
    }


def inference_pool(spec: LlmdSpec) -> Dict:
    return {
        "apiVersion": "inference.networking.k8s.io/v1",
        "kind": "InferencePool",
        "metadata": _meta(spec, spec.name),
        "spec": {
            "targetPorts": [{"number": MODEL_PORT}],
            "appProtocol": "http",
            "selector": {"matchLabels": {POOL_POD_LABEL: spec.name}},
            "endpointPickerRef": {
                "name": epp_service_name(spec.name),
                "port": {"number": EPP_GRPC_PORT},
                "failureMode": "FailOpen",
            },
        },
    }


def epp_plugin_config(spec: LlmdSpec) -> Dict:
    """The guide's precise prefix-cache routing config, for this model.

    Built as data and dumped, never string-formatted, so a model id cannot
    change the config's structure.
    """
    producer = "precise-prefix-cache-producer"
    return {
        "apiVersion": "llm-d.ai/v1",
        "kind": "EndpointPickerConfig",
        "plugins": [
            {"type": "token-producer", "parameters": {
                "modelName": spec.model,
                "vllm": {
                    "endpointDiscovery": {"attemptTimeout": "2s"},
                    "messagesRenderMode": "auto",
                    "responsesRenderMode": "auto",
                },
            }},
            {"type": "endpoint-notification-source"},
            {"type": producer, "parameters": {
                "tokenProcessorConfig": {"blockSizeTokens": BLOCK_SIZE_TOKENS, "hashAlgorithm": "xxh64"},
                "speculativeIndexing": True,
                "indexerConfig": {"kvBlockIndexConfig": {"enableMetrics": True}},
                "kvEventsConfig": {
                    "engineType": "vllm",
                    "topicFilter": "kv@",
                    "concurrency": 8,
                    "discoverPods": True,
                    "podDiscoveryConfig": {
                        "socketPort": KV_EVENTS_PORT,
                        "replaySocketPort": KV_REPLAY_PORT,
                    },
                },
            }},
            {"type": "inflight-load-producer", "parameters": {"prefixMatchInfoProducerName": producer}},
            # peakPrefillThroughput is the guide's H100 calibration (Qwen3-32B,
            # TP=2). It only sets when a cache-holding pod counts as too busy.
            {"type": "prefix-cache-affinity-filter", "parameters": {
                "prefixMatchInfoProducerName": producer,
                "peakPrefillThroughput": 15926,
            }},
            {"type": "token-load-scorer"},
        ],
        "dataLayer": {"sources": [{
            "pluginRef": "endpoint-notification-source",
            "extractors": [{"pluginRef": producer}],
        }]},
        "schedulingProfiles": [{
            "name": "default",
            "plugins": [{"pluginRef": "prefix-cache-affinity-filter"}, {"pluginRef": "token-load-scorer"}],
        }],
    }


def epp_config_map(spec: LlmdSpec) -> Dict:
    return {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": _meta(spec, epp_config_name(spec.name)),
        "data": {EPP_CONFIG_FILE: yaml.safe_dump(epp_plugin_config(spec), sort_keys=False)},
    }


def envoy_config(spec: LlmdSpec) -> Dict:
    """The chart's Envoy config with this deployment's fail-open fallback.

    Set by walking the parsed config, not by text substitution, so nothing else
    in it can be rewritten by accident.
    """
    cfg = copy.deepcopy(yaml.safe_load(_chart_envoy.ENVOY_YAML))
    clusters = [c for c in cfg["static_resources"]["clusters"] if c["name"] == "inference_pool_fallback"]
    if len(clusters) != 1:
        raise RuntimeError("vendored Envoy config has no unique inference_pool_fallback cluster")
    eps = clusters[0]["load_assignment"]["endpoints"]
    if len(eps) != 1 or len(eps[0]["lb_endpoints"]) != 1:
        raise RuntimeError("vendored Envoy fallback cluster has an unexpected shape")
    eps[0]["lb_endpoints"][0]["endpoint"]["address"]["socket_address"] = {
        "address": f"{fallback_service_name(spec.name)}.{spec.namespace}.svc.cluster.local",
        "port_value": MODEL_PORT,
    }
    return cfg


def envoy_config_map(spec: LlmdSpec) -> Dict:
    return {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": _meta(spec, envoy_config_name(spec.name)),
        "data": {"envoy.yaml": yaml.safe_dump(envoy_config(spec), sort_keys=False)},
    }


def _sleep_prestop(seconds: int) -> Dict:
    return {"preStop": {"sleep": {"seconds": seconds}}}


def epp_deployment(spec: LlmdSpec) -> Dict:
    pod_labels = {ROUTER_POD_LABEL: spec.name, INSTANCE_LABEL: spec.name}
    return {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": _meta(spec, epp_name(spec.name)),
        "spec": {
            # One replica: the KV index and the token-load scorer's in-flight
            # accounting are per process (llm-d guide note).
            "replicas": 1,
            "strategy": {"type": "Recreate"},
            "selector": {"matchLabels": {ROUTER_POD_LABEL: spec.name}},
            "template": {
                "metadata": {"labels": pod_labels},
                "spec": {
                    "serviceAccountName": EPP_SERVICE_ACCOUNT,
                    "terminationGracePeriodSeconds": 130,
                    "containers": [
                        {
                            "name": "envoy-proxy",
                            "image": ENVOY_IMAGE,
                            "imagePullPolicy": "IfNotPresent",
                            "command": ["envoy"],
                            "args": [
                                "--service-node", "envoy-sidecar",
                                "--log-level", "warn",
                                "--concurrency", "2",
                                "--drain-strategy", "immediate",
                                "--drain-time-s", "10",
                                "-c", "/etc/envoy/envoy.yaml",
                            ],
                            "env": [
                                _field_env("NS_NAME", "metadata.namespace"),
                                _field_env("POD_NAME", "metadata.name"),
                            ],
                            "ports": [
                                {"name": "http-8081", "containerPort": PROXY_PORT},
                                {"name": "metrics-19001", "containerPort": PROXY_READY_PORT},
                            ],
                            "readinessProbe": {
                                "httpGet": {"path": "/ready", "port": PROXY_READY_PORT},
                                "periodSeconds": 5, "failureThreshold": 1, "timeoutSeconds": 1,
                            },
                            "lifecycle": _sleep_prestop(5),
                            "resources": {
                                "requests": {"cpu": "250m", "memory": "128Mi"},
                                "limits": {"memory": "512Mi"},
                            },
                            "volumeMounts": [{"name": "config", "mountPath": "/etc/envoy", "readOnly": True}],
                        },
                        {
                            "name": "epp",
                            "image": EPP_IMAGE,
                            "imagePullPolicy": "IfNotPresent",
                            "args": [
                                "--pool-name", spec.name,
                                "--pool-namespace", spec.namespace,
                                "--pool-group", "inference.networking.k8s.io",
                                "--zap-encoder", "json",
                                "--config-file", f"/config/{EPP_CONFIG_FILE}",
                                "--grpc-health-port", str(EPP_HEALTH_PORT),
                                "--ha-enable-leader-election=false",
                                "--v=2",
                                "--tracing=false",
                            ],
                            "env": [
                                _field_env("NAMESPACE", "metadata.namespace"),
                                _field_env("POD_NAME", "metadata.name"),
                            ],
                            "ports": [
                                {"name": "grpc", "containerPort": EPP_GRPC_PORT},
                                {"name": "grpc-health", "containerPort": EPP_HEALTH_PORT},
                                {"name": "metrics", "containerPort": EPP_METRICS_PORT},
                            ],
                            "livenessProbe": {
                                "grpc": {"port": EPP_HEALTH_PORT, "service": "liveness"},
                                "initialDelaySeconds": 5, "periodSeconds": 10,
                            },
                            "readinessProbe": {
                                "grpc": {"port": EPP_HEALTH_PORT, "service": "readiness"},
                                "periodSeconds": 2,
                            },
                            "lifecycle": _sleep_prestop(5),
                            "resources": {
                                "requests": {"cpu": "250m", "memory": "256Mi"},
                                "limits": {"memory": "1Gi"},
                            },
                            "volumeMounts": [{"name": "plugins-config-volume", "mountPath": "/config"}],
                        },
                    ],
                    "volumes": [
                        {"name": "config", "configMap": {
                            "name": envoy_config_name(spec.name),
                            "items": [{"key": "envoy.yaml", "path": "envoy.yaml"}],
                        }},
                        {"name": "plugins-config-volume", "configMap": {"name": epp_config_name(spec.name)}},
                    ],
                },
            },
        },
    }


def epp_service(spec: LlmdSpec) -> Dict:
    return {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": _meta(spec, epp_service_name(spec.name)),
        "spec": {
            "type": "ClusterIP",
            "selector": {ROUTER_POD_LABEL: spec.name},
            "ports": [
                {"name": "grpc-ext-proc", "protocol": "TCP", "port": EPP_GRPC_PORT,
                 "appProtocol": "kubernetes.io/h2c"},
                {"name": "http-metrics", "protocol": "TCP", "port": EPP_METRICS_PORT},
            ],
        },
    }


def gateway_service(spec: LlmdSpec) -> Dict:
    return {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": _meta(spec, gateway_service_name(spec.name)),
        "spec": {
            "type": spec.service_type,
            "selector": {ROUTER_POD_LABEL: spec.name},
            "ports": [{"name": "http", "protocol": "TCP", "port": PROXY_PORT, "targetPort": PROXY_PORT}],
        },
    }


def build_objects(spec: LlmdSpec) -> List[Dict]:
    """All objects, in creation order: config and backends before the router,
    the externally reachable Service last."""
    return [
        epp_config_map(spec),
        envoy_config_map(spec),
        model_server_deployment(spec),
        fallback_service(spec),
        inference_pool(spec),
        epp_deployment(spec),
        epp_service(spec),
        gateway_service(spec),
    ]

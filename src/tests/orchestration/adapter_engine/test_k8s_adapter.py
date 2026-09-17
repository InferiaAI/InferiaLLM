"""Tests for the Kubernetes adapter: StatefulSet + Service, not a bare pod.

A Kubernetes deployment used to report RUNNING and then fail every request: the
adapter created a pod with no ports and returned ``k8s://namespace/podname``,
which nothing parses. These pin the contract that replaced it, so reverting any
part of it breaks a test rather than a customer.

The kubernetes client is mocked. Behaviour against a live cluster (self-healing,
a served response through the returned address) is integration territory and not
reachable from CI, which has no cluster.
"""
from unittest.mock import MagicMock, patch

import pytest

from orchestration import recipes
from providers.k8s.k8s_adapter import (
    KubernetesAdapter,
    _engine_port,
    _DEFAULT_CACHE_SIZE,
    _DEFAULT_KEEP_ALIVE,
    _MODEL_VOLUME_NAME,
)


# ---------------------------------------------------------------------------
# Engine port resolution
# ---------------------------------------------------------------------------
def test_explicit_port_wins():
    assert _engine_port({"port": 9999, "engine": "ollama"}) == 9999


def test_known_engines_have_ports():
    assert _engine_port({"engine": "ollama"}) == 11434
    # vLLM is GPU-only, so asking for its port without one is not a question
    # with an answer.
    assert _engine_port({"engine": "vllm", "gpu_allocated": 1}) == 8000


def test_engine_name_is_case_insensitive():
    assert _engine_port({"engine": "Ollama"}) == 11434


def test_unknown_engine_falls_back():
    """8000 is the default recipe's port, asserted as a literal so a change
    to it has to be made deliberately here too."""
    assert _engine_port({"engine": "something-new"}) == 8000
    assert _engine_port({}) == 8000
    assert _engine_port(None) == 8000


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
def _adapter():
    """An adapter with both API clients mocked and no kubeconfig loaded."""
    with patch("providers.k8s.k8s_adapter.config"):
        a = KubernetesAdapter()
    a.core = MagicMock()
    a.apps = MagicMock()
    a.custom = MagicMock()
    return a


def _service(svc_type="ClusterIP", port=11434, node_port=None):
    svc = MagicMock()
    svc.spec.type = svc_type
    p = MagicMock()
    p.port = port
    p.node_port = node_port
    svc.spec.ports = [p]
    return svc


def _metadata(**over):
    md = {
        "namespace": "default",
        "image": "ollama/ollama:latest",
        "cmd": ["ollama", "serve"],
        "engine": "ollama",
        "vcpu_allocated": 2,
        "ram_gb_allocated": 4,
        "gpu_allocated": 1,
    }
    md.update(over)
    return md


def _workload_of(a):
    """The StatefulSet the adapter just created."""
    return a.apps.create_namespaced_stateful_set.call_args.kwargs["body"]


def _container_of(a):
    return _workload_of(a).spec.template.spec.containers[0]


def _scaled_object_of(a):
    """The ScaledObject the adapter just created, or None."""
    call = a.custom.create_namespaced_custom_object.call_args
    return call.kwargs["body"] if call else None


def _claims_of(a):
    """The claim templates on that StatefulSet, or None when it has none."""
    return _workload_of(a).spec.volume_claim_templates


# ---------------------------------------------------------------------------
# provision_node
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_provision_creates_a_statefulset_and_service(monkeypatch):
    monkeypatch.delenv("K8S_SERVICE_TYPE", raising=False)
    a = _adapter()
    a.core.read_namespaced_service.return_value = _service()

    spec = await a.provision_node(
        provider_resource_id="node-1", pool_id="p1", metadata=_metadata(),
    )

    assert a.apps.create_namespaced_stateful_set.called, \
        "must create a StatefulSet"
    assert not a.apps.create_namespaced_deployment.called, \
        "a Deployment cannot give each replica its own volume"
    assert a.core.create_namespaced_service.called, "must create a Service"
    assert not a.core.create_namespaced_pod.called, "must not create a bare pod"
    assert spec["provider_instance_id"].startswith("inferia-worker-")


@pytest.mark.asyncio
async def test_provisioned_workload_has_port_and_probes(monkeypatch):
    monkeypatch.delenv("K8S_SERVICE_TYPE", raising=False)
    a = _adapter()
    a.core.read_namespaced_service.return_value = _service()

    await a.provision_node(
        provider_resource_id="node-1", pool_id="p1", metadata=_metadata(),
    )

    body = a.apps.create_namespaced_stateful_set.call_args.kwargs["body"]
    container = body.spec.template.spec.containers[0]

    assert container.ports, "a container with no ports cannot be reached"
    assert container.ports[0].container_port == 11434
    assert container.readiness_probe is not None, "readiness gates the Service"
    assert container.liveness_probe is not None, "liveness is what restarts it"
    assert body.spec.replicas == 1


@pytest.mark.asyncio
async def test_cmd_becomes_args_so_the_entrypoint_survives(monkeypatch):
    """Kubernetes ``command`` REPLACES the image entrypoint. ollama/ollama is
    ENTRYPOINT /bin/ollama + CMD ["serve"], so putting ["serve"] in command
    execs a binary named "serve" and crash-loops. Live-reproduced before this
    mapping was fixed."""
    monkeypatch.delenv("K8S_SERVICE_TYPE", raising=False)
    a = _adapter()
    a.core.read_namespaced_service.return_value = _service()

    await a.provision_node(
        provider_resource_id="node-1", pool_id="p1",
        metadata=_metadata(cmd=["serve"]),
    )

    container = a.apps.create_namespaced_stateful_set \
        .call_args.kwargs["body"].spec.template.spec.containers[0]

    assert container.args == ["serve"]
    assert container.command is None, \
        "setting command would discard the image entrypoint"


@pytest.mark.asyncio
async def test_explicit_command_overrides_the_entrypoint(monkeypatch):
    monkeypatch.delenv("K8S_SERVICE_TYPE", raising=False)
    a = _adapter()
    a.core.read_namespaced_service.return_value = _service()

    await a.provision_node(
        provider_resource_id="node-1", pool_id="p1",
        metadata=_metadata(command=["/bin/sh", "-c", "true"], cmd=None),
    )

    container = a.apps.create_namespaced_stateful_set \
        .call_args.kwargs["body"].spec.template.spec.containers[0]

    assert container.command == ["/bin/sh", "-c", "true"]


@pytest.mark.asyncio
async def test_no_cmd_leaves_the_image_to_run_as_built(monkeypatch):
    """The old default was ["sleep", "3600"], which as args to an engine image
    is nonsense. Omitting both is the correct default."""
    monkeypatch.delenv("K8S_SERVICE_TYPE", raising=False)
    a = _adapter()
    a.core.read_namespaced_service.return_value = _service()

    md = _metadata()
    md.pop("cmd")
    await a.provision_node(
        provider_resource_id="node-1", pool_id="p1", metadata=md,
    )

    container = a.apps.create_namespaced_stateful_set \
        .call_args.kwargs["body"].spec.template.spec.containers[0]

    assert container.command is None
    assert container.args is None


@pytest.mark.asyncio
async def test_workload_and_service_share_a_selector(monkeypatch):
    """A Service whose selector misses the pods routes to nothing."""
    monkeypatch.delenv("K8S_SERVICE_TYPE", raising=False)
    a = _adapter()
    a.core.read_namespaced_service.return_value = _service()

    await a.provision_node(
        provider_resource_id="node-1", pool_id="p1", metadata=_metadata(),
    )

    dep = a.apps.create_namespaced_stateful_set.call_args.kwargs["body"]
    svc = a.core.create_namespaced_service.call_args.kwargs["body"]

    assert dep.spec.selector.match_labels == svc.spec.selector
    assert dep.spec.template.metadata.labels["inferia-instance"] == \
        svc.spec.selector["inferia-instance"]


@pytest.mark.asyncio
async def test_service_type_defaults_to_cluster_ip(monkeypatch):
    # A default is only a default with nothing set; local dev exports this.
    monkeypatch.delenv("K8S_SERVICE_TYPE", raising=False)
    a = _adapter()
    a.core.read_namespaced_service.return_value = _service()

    await a.provision_node(
        provider_resource_id="node-1", pool_id="p1", metadata=_metadata(),
    )

    svc = a.core.create_namespaced_service.call_args.kwargs["body"]
    assert svc.spec.type == "ClusterIP"


@pytest.mark.asyncio
async def test_service_type_from_env(monkeypatch):
    monkeypatch.setenv("K8S_SERVICE_TYPE", "NodePort")
    a = _adapter()
    a.core.read_namespaced_service.return_value = _service(
        svc_type="NodePort", node_port=30001,
    )
    a.core.list_node.return_value = _nodes()

    await a.provision_node(
        provider_resource_id="node-1", pool_id="p1", metadata=_metadata(),
    )

    svc = a.core.create_namespaced_service.call_args.kwargs["body"]
    assert svc.spec.type == "NodePort"


@pytest.mark.asyncio
async def test_metadata_beats_env(monkeypatch):
    monkeypatch.setenv("K8S_SERVICE_TYPE", "NodePort")
    a = _adapter()
    a.core.read_namespaced_service.return_value = _service()

    await a.provision_node(
        provider_resource_id="node-1", pool_id="p1",
        metadata=_metadata(service_type="ClusterIP"),
    )

    svc = a.core.create_namespaced_service.call_args.kwargs["body"]
    assert svc.spec.type == "ClusterIP"


@pytest.mark.asyncio
async def test_failed_workload_rolls_back_the_service(monkeypatch):
    """The Service is created first, so it is what gets stranded."""
    monkeypatch.delenv("K8S_SERVICE_TYPE", raising=False)
    a = _adapter()
    a.apps.create_namespaced_stateful_set.side_effect = RuntimeError("boom")

    with pytest.raises(RuntimeError):
        await a.provision_node(
            provider_resource_id="node-1", pool_id="p1", metadata=_metadata(),
        )

    assert a.core.delete_namespaced_service.called, \
        "a Service with nothing behind it routes to nothing"


# ---------------------------------------------------------------------------
# Endpoint resolution
# ---------------------------------------------------------------------------
def _nodes(address="10.0.0.7", addr_type="InternalIP"):
    node = MagicMock()
    addr = MagicMock()
    addr.type = addr_type
    addr.address = address
    node.status.addresses = [addr]
    nodes = MagicMock()
    nodes.items = [node]
    return nodes


@pytest.mark.asyncio
async def test_cluster_ip_resolves_to_cluster_dns():
    a = _adapter()
    a.core.read_namespaced_service.return_value = _service(port=11434)

    url = await a._resolve_url("dep-1", "default")

    assert url == "http://dep-1.default.svc.cluster.local:11434"


@pytest.mark.asyncio
async def test_node_port_resolves_to_a_node_address():
    a = _adapter()
    a.core.read_namespaced_service.return_value = _service(
        svc_type="NodePort", node_port=31234,
    )
    a.core.list_node.return_value = _nodes(address="10.0.0.7")

    url = await a._resolve_url("dep-1", "default")

    assert url == "http://10.0.0.7:31234"


@pytest.mark.asyncio
async def test_node_port_without_any_node_address_raises():
    a = _adapter()
    a.core.read_namespaced_service.return_value = _service(
        svc_type="NodePort", node_port=31234,
    )
    empty = MagicMock()
    empty.items = []
    a.core.list_node.return_value = empty

    with pytest.raises(RuntimeError):
        await a._resolve_url("dep-1", "default")


# ---------------------------------------------------------------------------
# wait_for_ready
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_wait_for_ready_returns_a_real_address():
    """The regression that motivated all of this: the old adapter returned
    k8s://namespace/podname, which nothing in the codebase parses."""
    a = _adapter()
    dep = MagicMock()
    dep.status.ready_replicas = 1
    a.apps.read_namespaced_stateful_set.return_value = dep
    a.core.read_namespaced_service.return_value = _service(port=11434)

    url = await a.wait_for_ready(provider_instance_id="dep-1", timeout=5)

    assert url.startswith("http://"), "the router can only use an http address"
    assert "k8s://" not in url


@pytest.mark.asyncio
async def test_wait_for_ready_times_out_without_a_ready_replica():
    a = _adapter()
    dep = MagicMock()
    dep.status.ready_replicas = 0
    a.apps.read_namespaced_stateful_set.return_value = dep

    with pytest.raises(RuntimeError, match="no ready replica"):
        await a.wait_for_ready(provider_instance_id="dep-1", timeout=0)


@pytest.mark.asyncio
async def test_wait_for_ready_ignores_scheduled_but_unready_replicas():
    """A scheduled pod is not a serving one. Readiness must come from the
    probe, not from the pod existing."""
    a = _adapter()
    dep = MagicMock()
    dep.status.ready_replicas = None  # replicas exist, none ready
    a.apps.read_namespaced_stateful_set.return_value = dep

    with pytest.raises(RuntimeError):
        await a.wait_for_ready(provider_instance_id="dep-1", timeout=0)


# ---------------------------------------------------------------------------
# deprovision_node
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_deprovision_removes_the_workload_and_its_service():
    a = _adapter()

    await a.deprovision_node(provider_instance_id="dep-1")

    assert a.apps.delete_namespaced_stateful_set.called
    assert a.core.delete_namespaced_service.called, \
        "a leftover Service points at nothing"


@pytest.mark.asyncio
async def test_deprovision_tolerates_a_missing_workload():
    """Deprovision runs on rollback paths too, where one object may be gone."""
    from kubernetes import client as k8s_client

    a = _adapter()
    a.apps.delete_namespaced_stateful_set.side_effect = \
        k8s_client.exceptions.ApiException(status=404)

    await a.deprovision_node(provider_instance_id="dep-1")

    assert a.core.delete_namespaced_service.called, \
        "the Service must still be removed when the workload is already gone"


@pytest.mark.asyncio
async def test_deprovision_reraises_a_real_api_error():
    from kubernetes import client as k8s_client

    a = _adapter()
    a.apps.delete_namespaced_stateful_set.side_effect = \
        k8s_client.exceptions.ApiException(status=403)

    with pytest.raises(k8s_client.exceptions.ApiException):
        await a.deprovision_node(provider_instance_id="dep-1")


# ---------------------------------------------------------------------------
# get_logs
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_get_logs_resolves_the_pod_by_label():
    """provider_instance_id names the workload, not a pod, and there may be
    more than one pod behind it."""
    a = _adapter()
    pod = MagicMock()
    pod.metadata.name = "dep-1-5d4f7c9b8d-abcde"
    listing = MagicMock()
    listing.items = [pod]
    a.core.list_namespaced_pod.return_value = listing
    a.core.read_namespaced_pod_log.return_value = "line one\nline two"

    out = await a.get_logs(provider_instance_id="dep-1")

    assert out["logs"] == ["line one", "line two"]
    assert a.core.read_namespaced_pod_log.call_args.kwargs["name"] == \
        "dep-1-5d4f7c9b8d-abcde"
    selector = a.core.list_namespaced_pod.call_args.kwargs["label_selector"]
    assert selector == "inferia-instance=dep-1"


@pytest.mark.asyncio
async def test_get_logs_when_no_pod_exists_yet():
    a = _adapter()
    listing = MagicMock()
    listing.items = []
    a.core.list_namespaced_pod.return_value = listing

    out = await a.get_logs(provider_instance_id="dep-1")

    assert "No pod yet" in out["logs"][0]


# ---------------------------------------------------------------------------
# Capabilities
# ---------------------------------------------------------------------------
def test_readiness_timeout_allows_for_an_image_pull():
    """ollama/ollama is 9.18GB. The old 120s budget could not cover a first
    pull, so the deploy failed and the pod was torn down mid-download."""
    caps = KubernetesAdapter.CAPABILITIES
    assert caps.readiness_timeout_seconds >= 600


# ---------------------------------------------------------------------------
# Model loading
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_ollama_pulls_the_model_on_start(monkeypatch):
    monkeypatch.delenv("K8S_SERVICE_TYPE", raising=False)
    a = _adapter()
    a.core.read_namespaced_service.return_value = _service()

    await a.provision_node(
        provider_resource_id="node-1", pool_id="p1",
        metadata=_metadata(model_id="qwen2:0.5b"),
    )

    container = _container_of(a)
    script = " ".join(container.args or [])

    assert "ollama pull" in script, "the model is never fetched otherwise"
    assert "qwen2:0.5b" in script
    assert container.command == ["/bin/sh", "-c"], \
        "a pull needs a shell; the entrypoint cannot run one"


@pytest.mark.asyncio
async def test_ollama_model_name_is_shell_quoted(monkeypatch):
    """The model id comes from user input and lands in a shell command."""
    monkeypatch.delenv("K8S_SERVICE_TYPE", raising=False)
    a = _adapter()
    a.core.read_namespaced_service.return_value = _service()

    await a.provision_node(
        provider_resource_id="node-1", pool_id="p1",
        metadata=_metadata(model_id="evil; rm -rf /"),
    )

    script = " ".join(_container_of(a).args or [])

    assert "; rm -rf /" not in script.replace("'evil; rm -rf /'", ""), \
        "an unquoted model id is command injection"
    assert "'evil; rm -rf /'" in script


@pytest.mark.asyncio
async def test_ollama_startup_probe_checks_the_model(monkeypatch):
    """Ollama opens its port before the model exists, so a port check would
    pass with nothing served. Kubernetes runs no other probe until this one
    passes, which is why the slow check belongs here."""
    monkeypatch.delenv("K8S_SERVICE_TYPE", raising=False)
    a = _adapter()
    a.core.read_namespaced_service.return_value = _service()

    await a.provision_node(
        provider_resource_id="node-1", pool_id="p1",
        metadata=_metadata(model_id="qwen2:0.5b"),
    )

    probe = _container_of(a).startup_probe

    assert probe._exec is not None
    assert "ollama show" in " ".join(probe._exec.command)
    assert probe.failure_threshold * probe.period_seconds >= 600, \
        "a first model pull takes minutes"


@pytest.mark.asyncio
async def test_readiness_does_not_check_the_model(monkeypatch):
    """The regression this split exists for. Asking a busy engine whether it
    can serve gets no answer in time, so the replica the autoscaler just added
    is removed from the Service for being under load."""
    monkeypatch.delenv("K8S_SERVICE_TYPE", raising=False)
    a = _adapter()
    a.core.read_namespaced_service.return_value = _service()

    await a.provision_node(
        provider_resource_id="node-1", pool_id="p1",
        metadata=_metadata(model_id="qwen2:0.5b"),
    )

    probe = _container_of(a).readiness_probe

    assert probe._exec is None, "readiness must not run a command in the container"
    assert probe.http_get is not None
    assert probe.http_get.path == "/api/version", \
        "measured at 2-5ms under load, against 63-109ms for a model check"


@pytest.mark.asyncio
async def test_every_probe_sets_its_own_timeout(monkeypatch):
    """Kubernetes defaults timeoutSeconds to 1. That is what the exec probe
    could not meet on a loaded node."""
    monkeypatch.delenv("K8S_SERVICE_TYPE", raising=False)
    a = _adapter()
    a.core.read_namespaced_service.return_value = _service()

    await a.provision_node(
        provider_resource_id="node-1", pool_id="p1",
        metadata=_metadata(model_id="qwen2:0.5b"),
    )

    container = _container_of(a)

    for name in ("startup_probe", "readiness_probe", "liveness_probe"):
        probe = getattr(container, name)
        assert probe.timeout_seconds and probe.timeout_seconds > 1, name


@pytest.mark.asyncio
async def test_liveness_is_slower_to_act_than_readiness(monkeypatch):
    """Readiness removes a pod from the Service; liveness restarts it, which
    costs a full model load. They should not react at the same rate."""
    monkeypatch.delenv("K8S_SERVICE_TYPE", raising=False)
    a = _adapter()
    a.core.read_namespaced_service.return_value = _service()

    await a.provision_node(
        provider_resource_id="node-1", pool_id="p1",
        metadata=_metadata(model_id="qwen2:0.5b"),
    )

    container = _container_of(a)
    ready = container.readiness_probe
    live = container.liveness_probe

    ready_budget = ready.period_seconds * ready.failure_threshold
    live_budget = live.period_seconds * live.failure_threshold
    assert live_budget > ready_budget


@pytest.mark.asyncio
async def test_non_ollama_engine_keeps_the_tcp_probe(monkeypatch):
    """vLLM takes the model as a start argument and loads it itself."""
    monkeypatch.delenv("K8S_SERVICE_TYPE", raising=False)
    a = _adapter()
    a.core.read_namespaced_service.return_value = _service(port=8000)

    await a.provision_node(
        provider_resource_id="node-1", pool_id="p1",
        metadata=_metadata(engine="vllm", cmd=["--model", "x"]),
    )

    container = _container_of(a)

    assert container.readiness_probe.tcp_socket is not None
    assert container.args == ["--model", "x"]


# ---------------------------------------------------------------------------
# Model persistence
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_each_replica_gets_its_own_volume(monkeypatch):
    """A shared ReadWriteOnce volume binds to one node and blocks every
    replica placed anywhere else."""
    monkeypatch.delenv("K8S_SERVICE_TYPE", raising=False)
    a = _adapter()
    a.core.read_namespaced_service.return_value = _service()

    await a.provision_node(
        provider_resource_id="node-1", pool_id="p1", metadata=_metadata(),
    )

    claims = _claims_of(a)
    assert claims, "without a claim every replaced pod re-downloads the model"
    assert len(claims) == 1
    assert claims[0].metadata.name == _MODEL_VOLUME_NAME
    assert claims[0].spec.resources.requests["storage"] == _DEFAULT_CACHE_SIZE
    assert claims[0].spec.access_modes == ["ReadWriteOnce"]

    assert not a.core.create_namespaced_persistent_volume_claim.called, \
        "Kubernetes creates one claim per replica from the template"

    mount = _container_of(a).volume_mounts[0]
    assert mount.name == _MODEL_VOLUME_NAME, \
        "the mount name must match the template or nothing is mounted"
    assert mount.mount_path == "/root/.ollama", \
        "mounting anywhere else caches nothing"


@pytest.mark.asyncio
async def test_cache_size_is_overridable(monkeypatch):
    """A 70B model does not fit the default."""
    monkeypatch.delenv("K8S_SERVICE_TYPE", raising=False)
    a = _adapter()
    a.core.read_namespaced_service.return_value = _service()

    await a.provision_node(
        provider_resource_id="node-1", pool_id="p1",
        metadata=_metadata(model_cache_size="200Gi"),
    )

    assert _claims_of(a)[0].spec.resources.requests["storage"] == "200Gi"


@pytest.mark.asyncio
async def test_engine_with_no_known_model_dir_gets_no_volume(monkeypatch):
    monkeypatch.delenv("K8S_SERVICE_TYPE", raising=False)
    a = _adapter()
    a.core.read_namespaced_service.return_value = _service()

    await a.provision_node(
        provider_resource_id="node-1", pool_id="p1",
        metadata=_metadata(engine="something-new"),
    )

    assert _claims_of(a) is None
    assert _container_of(a).volume_mounts is None


@pytest.mark.asyncio
async def test_deprovision_removes_every_replica_volume():
    """Claims outlive the StatefulSet by design, and their names carry a
    replica ordinal, so they go by label."""
    a = _adapter()

    await a.deprovision_node(provider_instance_id="dep-1")

    call = a.core.delete_collection_namespaced_persistent_volume_claim.call_args
    assert call is not None, "claims left behind leak their storage"
    assert call.kwargs["label_selector"] == "inferia-instance=dep-1"


@pytest.mark.asyncio
async def test_ollama_without_a_model_id_is_left_alone(monkeypatch):
    """Nothing to pull, so the startup override does not apply. The volume
    still does, since the engine writes weights there either way."""
    monkeypatch.delenv("K8S_SERVICE_TYPE", raising=False)
    a = _adapter()
    a.core.read_namespaced_service.return_value = _service()

    await a.provision_node(
        provider_resource_id="node-1", pool_id="p1", metadata=_metadata(),
    )

    container = _container_of(a)

    assert container.startup_probe._exec is None, \
        "nothing to check for: no model was named"
    assert container.readiness_probe.http_get.path == "/api/version"
    assert _claims_of(a), "the engine writes weights there either way"


@pytest.mark.asyncio
async def test_ollama_model_stays_resident(monkeypatch):
    """Ollama unloads 5 minutes after the last request, so the next one waits
    for a reload: 14.9s cold against 0.34s resident on a deployed qwen2:0.5b."""
    monkeypatch.delenv("K8S_SERVICE_TYPE", raising=False)
    a = _adapter()
    a.core.read_namespaced_service.return_value = _service()

    await a.provision_node(
        provider_resource_id="node-1", pool_id="p1",
        metadata=_metadata(model_id="qwen2:0.5b"),
    )

    env = {e.name: e.value for e in _container_of(a).env or []}

    assert env.get("OLLAMA_KEEP_ALIVE") == _DEFAULT_KEEP_ALIVE
    assert _DEFAULT_KEEP_ALIVE == "-1", "anything else still unloads"


@pytest.mark.asyncio
async def test_keep_alive_is_overridable(monkeypatch):
    """A node hosting many models may want them evicted."""
    monkeypatch.delenv("K8S_SERVICE_TYPE", raising=False)
    a = _adapter()
    a.core.read_namespaced_service.return_value = _service()

    await a.provision_node(
        provider_resource_id="node-1", pool_id="p1",
        metadata=_metadata(model_id="qwen2:0.5b", keep_alive="10m"),
    )

    env = {e.name: e.value for e in _container_of(a).env or []}

    assert env.get("OLLAMA_KEEP_ALIVE") == "10m"


@pytest.mark.asyncio
async def test_non_ollama_engine_gets_no_keep_alive(monkeypatch):
    monkeypatch.delenv("K8S_SERVICE_TYPE", raising=False)
    a = _adapter()
    a.core.read_namespaced_service.return_value = _service(port=8000)

    await a.provision_node(
        provider_resource_id="node-1", pool_id="p1",
        metadata=_metadata(engine="vllm", cmd=["--model", "x"]),
    )

    env = {e.name for e in (_container_of(a).env or [])}

    assert "OLLAMA_KEEP_ALIVE" not in env


# ---------------------------------------------------------------------------
# Scaling
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_the_workload_names_a_service(monkeypatch):
    """A StatefulSet without serviceName is rejected by the API server."""
    monkeypatch.delenv("K8S_SERVICE_TYPE", raising=False)
    a = _adapter()
    a.core.read_namespaced_service.return_value = _service()

    spec = await a.provision_node(
        provider_resource_id="node-1", pool_id="p1", metadata=_metadata(),
    )

    svc = a.core.create_namespaced_service.call_args.kwargs["body"]
    assert _workload_of(a).spec.service_name == spec["provider_instance_id"]
    assert svc.metadata.name == spec["provider_instance_id"]


@pytest.mark.asyncio
async def test_replicas_start_in_parallel(monkeypatch):
    """The default would serialise one model load per replica."""
    monkeypatch.delenv("K8S_SERVICE_TYPE", raising=False)
    a = _adapter()
    a.core.read_namespaced_service.return_value = _service()

    await a.provision_node(
        provider_resource_id="node-1", pool_id="p1", metadata=_metadata(),
    )

    assert _workload_of(a).spec.pod_management_policy == "Parallel"


@pytest.mark.asyncio
async def test_the_service_is_created_before_the_workload(monkeypatch):
    """The other way round leaves a running workload to roll back."""
    monkeypatch.delenv("K8S_SERVICE_TYPE", raising=False)
    a = _adapter()
    a.core.read_namespaced_service.return_value = _service()
    order = []
    a.core.create_namespaced_service.side_effect = \
        lambda **kw: order.append("service")
    a.apps.create_namespaced_stateful_set.side_effect = \
        lambda **kw: order.append("workload")

    await a.provision_node(
        provider_resource_id="node-1", pool_id="p1", metadata=_metadata(),
    )

    assert order == ["service", "workload"]


# ---------------------------------------------------------------------------
# Autoscaling
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_no_scaled_object_unless_autoscaling_is_on(monkeypatch):
    monkeypatch.delenv("K8S_SERVICE_TYPE", raising=False)
    a = _adapter()
    a.core.read_namespaced_service.return_value = _service()

    await a.provision_node(
        provider_resource_id="node-1", pool_id="p1", metadata=_metadata(),
    )

    assert not a.custom.create_namespaced_custom_object.called


@pytest.mark.asyncio
async def test_autoscaling_creates_a_scaled_object(monkeypatch):
    monkeypatch.delenv("K8S_SERVICE_TYPE", raising=False)
    a = _adapter()
    a.core.read_namespaced_service.return_value = _service()

    spec = await a.provision_node(
        provider_resource_id="node-1", pool_id="p1",
        metadata=_metadata(autoscale=True, deployment_id="dep-uuid"),
    )
    name = spec["provider_instance_id"]

    call = a.custom.create_namespaced_custom_object.call_args
    assert call.kwargs["plural"] == "scaledobjects"
    body = call.kwargs["body"]

    assert body["spec"]["scaleTargetRef"] == {
        "kind": "StatefulSet", "name": name,
    }, "scaling anything but the StatefulSet changes nothing"
    assert body["spec"]["minReplicaCount"] == 1


@pytest.mark.asyncio
async def test_the_query_filters_on_the_deployment_id(monkeypatch):
    """The metrics are labelled with the deployment id, not the node name, so
    a query built from the wrong one matches no series and never scales."""
    monkeypatch.delenv("K8S_SERVICE_TYPE", raising=False)
    a = _adapter()
    a.core.read_namespaced_service.return_value = _service()

    await a.provision_node(
        provider_resource_id="node-1", pool_id="p1",
        metadata=_metadata(autoscale=True, deployment_id="dep-uuid"),
    )

    query = _scaled_object_of(a)["spec"]["triggers"][0]["metadata"]["query"]

    assert 'deployment="dep-uuid"' in query
    assert "inferia_upstream_in_flight_requests" in query


@pytest.mark.asyncio
async def test_the_query_carries_its_guards(monkeypatch):
    """A latency guard and a minimum sample count, both as multipliers in the
    one query. KEDA takes the maximum across triggers, so a second trigger
    could only raise the replica count and never hold it down."""
    monkeypatch.delenv("K8S_SERVICE_TYPE", raising=False)
    a = _adapter()
    a.core.read_namespaced_service.return_value = _service()

    await a.provision_node(
        provider_resource_id="node-1", pool_id="p1",
        metadata=_metadata(
            autoscale=True, deployment_id="d", autoscale_p95_seconds=7,
            autoscale_min_samples=9,
        ),
    )

    trigger = _scaled_object_of(a)["spec"]["triggers"][0]
    query = trigger["metadata"]["query"]

    assert "histogram_quantile(0.95" in query
    assert "> bool 7" in query, "latency guard"
    assert "> bool 9" in query, "minimum sample count"
    assert len(_scaled_object_of(a)["spec"]["triggers"]) == 1


@pytest.mark.asyncio
async def test_scaling_thresholds_are_overridable(monkeypatch):
    monkeypatch.delenv("K8S_SERVICE_TYPE", raising=False)
    a = _adapter()
    a.core.read_namespaced_service.return_value = _service()

    await a.provision_node(
        provider_resource_id="node-1", pool_id="p1",
        metadata=_metadata(
            autoscale=True, deployment_id="d",
            autoscale_max_replicas=9, autoscale_in_flight_per_replica=4,
        ),
    )

    spec = _scaled_object_of(a)["spec"]
    assert spec["maxReplicaCount"] == 9
    assert spec["triggers"][0]["metadata"]["threshold"] == "4"


@pytest.mark.asyncio
async def test_scale_down_does_not_wait_five_minutes(monkeypatch):
    """The Kubernetes default is 300s, which is slower than the load it is
    reacting to."""
    monkeypatch.delenv("K8S_SERVICE_TYPE", raising=False)
    a = _adapter()
    a.core.read_namespaced_service.return_value = _service()

    await a.provision_node(
        provider_resource_id="node-1", pool_id="p1",
        metadata=_metadata(autoscale=True, deployment_id="d"),
    )

    behavior = (
        _scaled_object_of(a)["spec"]["advanced"]
        ["horizontalPodAutoscalerConfig"]["behavior"]
    )
    assert behavior["scaleDown"]["stabilizationWindowSeconds"] < 300


@pytest.mark.asyncio
async def test_a_cluster_without_keda_still_deploys(monkeypatch):
    """Autoscaling is optional. Losing it must not cost the deployment."""
    monkeypatch.delenv("K8S_SERVICE_TYPE", raising=False)
    a = _adapter()
    a.core.read_namespaced_service.return_value = _service()
    a.custom.create_namespaced_custom_object.side_effect = RuntimeError("no crd")

    spec = await a.provision_node(
        provider_resource_id="node-1", pool_id="p1",
        metadata=_metadata(autoscale=True, deployment_id="d"),
    )

    assert spec["provider_instance_id"].startswith("inferia-worker-")


@pytest.mark.asyncio
async def test_deprovision_removes_the_scaled_object_first():
    """A ScaledObject outliving its workload leaves KEDA reconciling something
    that is gone, and it writes to the StatefulSet while it still exists."""
    a = _adapter()
    order = []
    a.custom.delete_namespaced_custom_object.side_effect = \
        lambda **kw: order.append("scaledobject")
    a.apps.delete_namespaced_stateful_set.side_effect = \
        lambda **kw: order.append("statefulset")

    await a.deprovision_node(provider_instance_id="dep-1")

    assert order == ["scaledobject", "statefulset"]


@pytest.mark.asyncio
async def test_deprovision_tolerates_no_scaled_object():
    """Deprovision cannot tell whether autoscaling was ever enabled, so it
    always tries, and a 404 is the ordinary case."""
    from kubernetes import client as k8s_client

    a = _adapter()
    a.custom.delete_namespaced_custom_object.side_effect = \
        k8s_client.exceptions.ApiException(status=404)

    await a.deprovision_node(provider_instance_id="dep-1")

    assert a.apps.delete_namespaced_stateful_set.called, \
        "the workload must still be removed"


# ---------------------------------------------------------------------------
# Resources
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_a_cpu_engine_reserves_little_and_is_not_capped(monkeypatch):
    """A ceiling is what made this engine slow; the reservation is what stops
    it scheduling. Only the ceiling needed removing."""
    monkeypatch.delenv("K8S_SERVICE_TYPE", raising=False)
    a = _adapter()
    a.core.read_namespaced_service.return_value = _service()

    md = _metadata()
    md["gpu_allocated"] = 0
    md.pop("vcpu_allocated")
    await a.provision_node(
        provider_resource_id="node-1", pool_id="p1", metadata=md,
    )

    res = _container_of(a).resources

    assert "cpu" not in res.limits
    assert res.requests["cpu"] == "1"


@pytest.mark.asyncio
async def test_a_gpu_engine_gets_no_cpu_ceiling(monkeypatch):
    """Throttling an engine whose CPU only does tokenisation and scheduling
    produces the latency spikes an SLO exists to prevent, and buys nothing:
    under contention the kernel already shares CPU in proportion to requests."""
    monkeypatch.delenv("K8S_SERVICE_TYPE", raising=False)
    a = _adapter()
    a.core.read_namespaced_service.return_value = _service()

    await a.provision_node(
        provider_resource_id="node-1", pool_id="p1", metadata=_metadata(),
    )

    res = _container_of(a).resources

    assert "cpu" not in res.limits
    assert res.requests["cpu"], "a reservation is still needed to schedule"


@pytest.mark.asyncio
async def test_a_recipe_can_ask_for_a_cpu_ceiling(tmp_path, monkeypatch):
    """No shipped recipe sets this, because a ceiling only pays for itself
    where the kubelet pins cores. A cluster configured for that opts in
    through pool config, so the branch has to work."""
    monkeypatch.delenv("K8S_SERVICE_TYPE", raising=False)
    (tmp_path / "pinned.yaml").write_text(
        "engines:\n"
        "  ollama:\n"
        "    port: 11434\n"
        "    profiles:\n"
        "      cpu:\n"
        "        cpu_request: '3'\n"
        "        cpu_burstable: false\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("INFERIA_RECIPES_DIR", str(tmp_path))
    recipes.reload()

    a = _adapter()
    a.core.read_namespaced_service.return_value = _service()

    md = _metadata()
    md["gpu_allocated"] = 0
    md.pop("vcpu_allocated")
    await a.provision_node(
        provider_resource_id="node-1", pool_id="p1", metadata=md,
    )
    recipes.reload()

    res = _container_of(a).resources

    assert res.limits["cpu"] == "3"
    assert res.requests["cpu"] == "3", "a ceiling below the floor is not a thing"


@pytest.mark.asyncio
async def test_memory_always_keeps_its_ceiling(monkeypatch):
    """An OOM kill is contained; a node running out of memory is not."""
    monkeypatch.delenv("K8S_SERVICE_TYPE", raising=False)
    a = _adapter()
    a.core.read_namespaced_service.return_value = _service()

    await a.provision_node(
        provider_resource_id="node-1", pool_id="p1", metadata=_metadata(),
    )

    res = _container_of(a).resources

    assert res.limits["memory"] == res.requests["memory"]


@pytest.mark.asyncio
async def test_the_gpu_limit_still_matches_the_request(monkeypatch):
    """Extended resources are not burstable: Kubernetes requires request and
    limit to be equal, so removing the CPU ceiling must not touch this."""
    monkeypatch.delenv("K8S_SERVICE_TYPE", raising=False)
    a = _adapter()
    a.core.read_namespaced_service.return_value = _service()

    await a.provision_node(
        provider_resource_id="node-1", pool_id="p1",
        metadata=_metadata(gpu_allocated=2),
    )

    assert _container_of(a).resources.limits["nvidia.com/gpu"] == "2"


@pytest.mark.asyncio
async def test_deployment_config_still_wins_over_the_recipe(monkeypatch):
    """The recipe carries a floor, not a sizing decision."""
    monkeypatch.delenv("K8S_SERVICE_TYPE", raising=False)
    a = _adapter()
    a.core.read_namespaced_service.return_value = _service()

    await a.provision_node(
        provider_resource_id="node-1", pool_id="p1",
        metadata=_metadata(vcpu_allocated=7),
    )

    assert _container_of(a).resources.requests["cpu"] == "7"


@pytest.mark.asyncio
async def test_the_spec_reports_a_whole_number_of_cores(monkeypatch):
    """Kubernetes takes a quantity string and accepts fractions like "500m";
    compute_inventory.vcpu_total is an integer column. Returning the quantity
    here builds a working pod and then fails the row that records it, so the
    deployment reaches Ready and is marked FAILED."""
    monkeypatch.delenv("K8S_SERVICE_TYPE", raising=False)
    a = _adapter()
    a.core.read_namespaced_service.return_value = _service()

    md = _metadata()
    md["gpu_allocated"] = 0
    md.pop("vcpu_allocated")
    spec = await a.provision_node(
        provider_resource_id="node-1", pool_id="p1", metadata=md,
    )

    assert isinstance(spec["vcpu_total"], int)
    assert isinstance(_container_of(a).resources.requests["cpu"], str)


@pytest.mark.asyncio
async def test_the_pull_policy_is_set_explicitly(monkeypatch):
    """Left unset, Kubernetes infers it from the tag: `latest` means Always,
    and Always fails the container when the registry cannot be reached even
    if the image is already on the node."""
    monkeypatch.delenv("K8S_SERVICE_TYPE", raising=False)
    a = _adapter()
    a.core.read_namespaced_service.return_value = _service()

    await a.provision_node(
        provider_resource_id="node-1", pool_id="p1", metadata=_metadata(),
    )

    assert _container_of(a).image_pull_policy == "IfNotPresent"


@pytest.mark.asyncio
async def test_recipe_env_reaches_the_container(tmp_path, monkeypatch):
    """Recipe env has to arrive alongside the engine's own, not replace it."""
    monkeypatch.delenv("K8S_SERVICE_TYPE", raising=False)
    (tmp_path / "env.yaml").write_text(
        "engines:\n"
        "  ollama:\n"
        "    port: 11434\n"
        "    env:\n"
        "      FROM_RECIPE: 42\n"
        "    profiles:\n"
        "      cpu: {}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("INFERIA_RECIPES_DIR", str(tmp_path))
    recipes.reload()

    a = _adapter()
    a.core.read_namespaced_service.return_value = _service()

    md = _metadata(model_id="qwen2:0.5b")
    md["gpu_allocated"] = 0
    await a.provision_node(
        provider_resource_id="node-1", pool_id="p1", metadata=md,
    )
    recipes.reload()

    env = {e.name: e.value for e in (_container_of(a).env or [])}

    assert env.get("FROM_RECIPE") == "42", \
        "an unquoted YAML number is still a string to Kubernetes"
    assert env.get("OLLAMA_KEEP_ALIVE") == "-1", "the engine's own env survives"


@pytest.mark.asyncio
async def test_an_engine_without_the_hardware_it_needs_is_rejected(monkeypatch):
    """vLLM's CPU backend is a separate image. Provisioning must fail rather
    than produce a pod that schedules and cannot start."""
    monkeypatch.delenv("K8S_SERVICE_TYPE", raising=False)
    a = _adapter()
    a.core.read_namespaced_service.return_value = _service(port=8000)

    with pytest.raises(recipes.UnknownProfile):
        await a.provision_node(
            provider_resource_id="node-1", pool_id="p1",
            metadata=_metadata(engine="vllm", gpu_allocated=0, cmd=["--model", "x"]),
        )

    assert not a.apps.create_namespaced_stateful_set.called


# ---------------------------------------------------------------------------
# vLLM
# ---------------------------------------------------------------------------
def _vllm_metadata(**over):
    """What a dashboard vLLM deployment sends: no image and no cmd."""
    md = {
        "namespace": "default",
        "engine": "vllm",
        "model_id": "Qwen/Qwen2.5-0.5B-Instruct",
        "gpu": True,
        "gpu_allocated": 1,
        "vcpu_allocated": 2,
        "ram_gb_allocated": 8,
    }
    md.update(over)
    return md


@pytest.mark.asyncio
async def test_vllm_runs_its_image_when_the_deployment_names_none(monkeypatch):
    monkeypatch.delenv("K8S_SERVICE_TYPE", raising=False)
    a = _adapter()
    a.core.read_namespaced_service.return_value = _service(port=8000)

    await a.provision_node(
        provider_resource_id="node-1", pool_id="p1", metadata=_vllm_metadata(),
    )

    container = _container_of(a)

    assert container.image == "docker.io/vllm/vllm-openai:v0.22.1"
    assert container.command is None, "the image entrypoint is `vllm serve`"
    assert container.args == [
        "Qwen/Qwen2.5-0.5B-Instruct",
        "--served-model-name", "Qwen/Qwen2.5-0.5B-Instruct",
        "--host", "0.0.0.0",
        "--port", "8000",
    ]


@pytest.mark.asyncio
async def test_vllm_is_served_under_the_name_the_router_sends(monkeypatch):
    """vLLM answers 404 to any other name."""
    monkeypatch.delenv("K8S_SERVICE_TYPE", raising=False)
    a = _adapter()
    a.core.read_namespaced_service.return_value = _service(port=8000)

    md = _vllm_metadata()
    await a.provision_node(provider_resource_id="node-1", pool_id="p1", metadata=md)

    args = _container_of(a).args
    served = args[args.index("--served-model-name") + 1]

    assert served == md["model_id"]


@pytest.mark.asyncio
async def test_vllm_max_model_len_is_passed_only_when_set(monkeypatch):
    """A value above the model's own context stops vLLM from starting."""
    monkeypatch.delenv("K8S_SERVICE_TYPE", raising=False)
    a = _adapter()
    a.core.read_namespaced_service.return_value = _service(port=8000)

    await a.provision_node(
        provider_resource_id="node-1", pool_id="p1", metadata=_vllm_metadata(),
    )
    assert "--max-model-len" not in _container_of(a).args

    await a.provision_node(
        provider_resource_id="node-2", pool_id="p1",
        metadata=_vllm_metadata(max_model_len=4096),
    )
    args = _container_of(a).args
    assert args[args.index("--max-model-len") + 1] == "4096"


@pytest.mark.asyncio
async def test_a_deployment_image_wins_over_the_recipe(monkeypatch):
    monkeypatch.delenv("K8S_SERVICE_TYPE", raising=False)
    a = _adapter()
    a.core.read_namespaced_service.return_value = _service(port=8000)

    await a.provision_node(
        provider_resource_id="node-1", pool_id="p1",
        metadata=_vllm_metadata(image="registry.local/vllm:pinned"),
    )

    assert _container_of(a).image == "registry.local/vllm:pinned"


@pytest.mark.asyncio
async def test_ollama_gets_its_image_without_the_dashboard(monkeypatch):
    monkeypatch.delenv("K8S_SERVICE_TYPE", raising=False)
    a = _adapter()
    a.core.read_namespaced_service.return_value = _service()

    md = _metadata(model_id="qwen2:0.5b")
    md.pop("image")
    md.pop("cmd")
    await a.provision_node(provider_resource_id="node-1", pool_id="p1", metadata=md)

    assert _container_of(a).image == "ollama/ollama:latest"


@pytest.mark.asyncio
async def test_deployment_env_reaches_the_container(monkeypatch):
    monkeypatch.delenv("K8S_SERVICE_TYPE", raising=False)
    a = _adapter()
    a.core.read_namespaced_service.return_value = _service(port=8000)

    await a.provision_node(
        provider_resource_id="node-1", pool_id="p1",
        metadata=_vllm_metadata(env={"HF_TOKEN": "hf_x", "BATCH": 8}),
    )

    env = {e.name: e.value for e in _container_of(a).env}

    assert env["HF_TOKEN"] == "hf_x"
    assert env["BATCH"] == "8", "Kubernetes takes env values as strings"
    assert "LD_LIBRARY_PATH" in env, "recipe env is kept alongside"


@pytest.mark.asyncio
async def test_deployment_env_wins_over_the_recipe(monkeypatch):
    monkeypatch.delenv("K8S_SERVICE_TYPE", raising=False)
    a = _adapter()
    a.core.read_namespaced_service.return_value = _service(port=8000)

    await a.provision_node(
        provider_resource_id="node-1", pool_id="p1",
        metadata=_vllm_metadata(env={"LD_LIBRARY_PATH": "/opt/driver"}),
    )

    names = [e.name for e in _container_of(a).env]
    env = {e.name: e.value for e in _container_of(a).env}

    assert env["LD_LIBRARY_PATH"] == "/opt/driver"
    assert names.count("LD_LIBRARY_PATH") == 1, "one value, not two to guess between"


@pytest.mark.asyncio
async def test_deployment_env_can_set_the_ollama_keep_alive(monkeypatch):
    monkeypatch.delenv("K8S_SERVICE_TYPE", raising=False)
    a = _adapter()
    a.core.read_namespaced_service.return_value = _service()

    await a.provision_node(
        provider_resource_id="node-1", pool_id="p1",
        metadata=_metadata(model_id="qwen2:0.5b", env={"OLLAMA_KEEP_ALIVE": "10m"}),
    )

    names = [e.name for e in _container_of(a).env]
    env = {e.name: e.value for e in _container_of(a).env}

    assert env["OLLAMA_KEEP_ALIVE"] == "10m"
    assert names.count("OLLAMA_KEEP_ALIVE") == 1


def test_vllm_matches_the_nosana_path():
    """The library path works around a bug in this exact image."""
    import inspect

    from providers.nosana.job_builder import (
        CUDA_DRIVER_LD_LIBRARY_PATH,
        create_vllm_job,
    )

    recipe = recipes.resolve("vllm", "gpu")
    nosana_image = inspect.signature(create_vllm_job).parameters["image"].default

    assert recipe.image == nosana_image
    assert recipe.env["LD_LIBRARY_PATH"] == CUDA_DRIVER_LD_LIBRARY_PATH


# ---------------------------------------------------------------------------
# GPU and memory from a real deployment row
# ---------------------------------------------------------------------------
def _dashboard_row(engine, model_id, gpu_per_replica=1, **config):
    """A deployment row as the dashboard creates it: the GPU count is a
    column, and the configuration carries no resource fields."""
    import json
    import uuid

    cfg = {"model_id": model_id, "engine": engine, "gpu": True, **config}
    return {
        "configuration": json.dumps(cfg),
        "inference_model": model_id,
        "engine": engine,
        "model_name": "test",
        "deployment_id": uuid.uuid4(),
        "gpu_per_replica": gpu_per_replica,
    }


@pytest.mark.asyncio
async def test_a_dashboard_vllm_deployment_gets_a_gpu(monkeypatch):
    """Through the real metadata builder: this raised UnknownProfile because
    the GPU count never arrived."""
    from orchestration.models.model_deployment.direct_provision import (
        _build_metadata,
    )

    monkeypatch.delenv("K8S_SERVICE_TYPE", raising=False)
    a = _adapter()
    a.core.read_namespaced_service.return_value = _service(port=8000)

    md = _build_metadata(_dashboard_row("vllm", "Qwen/Qwen2.5-0.5B-Instruct"))
    await a.provision_node(provider_resource_id="node-1", pool_id="p1", metadata=md)

    res = _container_of(a).resources

    assert res.limits["nvidia.com/gpu"] == "1"
    assert res.limits["memory"] == "8Gi"


@pytest.mark.asyncio
async def test_a_dashboard_ollama_deployment_gets_its_gpu(monkeypatch):
    """It ran on CPU on a GPU node, with nothing to show for it."""
    from orchestration.models.model_deployment.direct_provision import (
        _build_metadata,
    )

    monkeypatch.delenv("K8S_SERVICE_TYPE", raising=False)
    a = _adapter()
    a.core.read_namespaced_service.return_value = _service()

    md = _build_metadata(_dashboard_row("ollama", "qwen2:0.5b"))
    await a.provision_node(provider_resource_id="node-1", pool_id="p1", metadata=md)

    assert _container_of(a).resources.limits["nvidia.com/gpu"] == "1"


@pytest.mark.asyncio
async def test_deployment_memory_wins_over_the_recipe(monkeypatch):
    from orchestration.models.model_deployment.direct_provision import (
        _build_metadata,
    )

    monkeypatch.delenv("K8S_SERVICE_TYPE", raising=False)
    a = _adapter()
    a.core.read_namespaced_service.return_value = _service(port=8000)

    md = _build_metadata(
        _dashboard_row("vllm", "Qwen/Qwen3-32B", ram_gb_allocated=48)
    )
    spec = await a.provision_node(
        provider_resource_id="node-1", pool_id="p1", metadata=md,
    )

    assert _container_of(a).resources.limits["memory"] == "48Gi"
    assert spec["ram_gb_total"] == 48


@pytest.mark.asyncio
async def test_an_engine_without_a_memory_floor_keeps_the_old_default(monkeypatch):
    """Ollama replicas share one node when KEDA scales them, so its memory
    is not raised here."""
    monkeypatch.delenv("K8S_SERVICE_TYPE", raising=False)
    a = _adapter()
    a.core.read_namespaced_service.return_value = _service()

    md = _metadata()
    md.pop("ram_gb_allocated")
    spec = await a.provision_node(
        provider_resource_id="node-1", pool_id="p1", metadata=md,
    )

    assert _container_of(a).resources.limits["memory"] == "1Gi"
    assert isinstance(spec["ram_gb_total"], int)

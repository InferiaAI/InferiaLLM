"""Tests for the Kubernetes adapter — Deployment + Service, not a bare pod.

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

from providers.k8s.k8s_adapter import (
    KubernetesAdapter,
    _engine_port,
    _DEFAULT_ENGINE_PORT,
)


# ---------------------------------------------------------------------------
# Engine port resolution
# ---------------------------------------------------------------------------
def test_explicit_port_wins():
    assert _engine_port({"port": 9999, "engine": "ollama"}) == 9999


def test_known_engines_have_ports():
    assert _engine_port({"engine": "ollama"}) == 11434
    assert _engine_port({"engine": "vllm"}) == 8000


def test_engine_name_is_case_insensitive():
    assert _engine_port({"engine": "Ollama"}) == 11434


def test_unknown_engine_falls_back():
    assert _engine_port({"engine": "something-new"}) == _DEFAULT_ENGINE_PORT
    assert _engine_port({}) == _DEFAULT_ENGINE_PORT
    assert _engine_port(None) == _DEFAULT_ENGINE_PORT


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
def _adapter():
    """An adapter with both API clients mocked and no kubeconfig loaded."""
    with patch("providers.k8s.k8s_adapter.config"):
        a = KubernetesAdapter()
    a.core = MagicMock()
    a.apps = MagicMock()
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


# ---------------------------------------------------------------------------
# provision_node
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_provision_creates_deployment_and_service(monkeypatch):
    monkeypatch.delenv("K8S_SERVICE_TYPE", raising=False)
    a = _adapter()
    a.core.read_namespaced_service.return_value = _service()

    spec = await a.provision_node(
        provider_resource_id="node-1", pool_id="p1", metadata=_metadata(),
    )

    assert a.apps.create_namespaced_deployment.called, "must create a Deployment"
    assert a.core.create_namespaced_service.called, "must create a Service"
    assert not a.core.create_namespaced_pod.called, "must not create a bare pod"
    assert spec["provider_instance_id"].startswith("inferia-worker-")


@pytest.mark.asyncio
async def test_provisioned_deployment_has_port_and_probes(monkeypatch):
    monkeypatch.delenv("K8S_SERVICE_TYPE", raising=False)
    a = _adapter()
    a.core.read_namespaced_service.return_value = _service()

    await a.provision_node(
        provider_resource_id="node-1", pool_id="p1", metadata=_metadata(),
    )

    body = a.apps.create_namespaced_deployment.call_args.kwargs["body"]
    container = body.spec.template.spec.containers[0]

    assert container.ports, "a container with no ports cannot be reached"
    assert container.ports[0].container_port == 11434
    assert container.readiness_probe is not None, "readiness gates the Service"
    assert container.liveness_probe is not None, "liveness is what restarts it"
    assert body.spec.replicas == 1


@pytest.mark.asyncio
async def test_cmd_becomes_args_so_the_entrypoint_survives(monkeypatch):
    monkeypatch.delenv("K8S_SERVICE_TYPE", raising=False)
    """Kubernetes ``command`` REPLACES the image entrypoint. ollama/ollama is
    ENTRYPOINT /bin/ollama + CMD ["serve"], so putting ["serve"] in command
    execs a binary named "serve" and crash-loops. Live-reproduced before this
    mapping was fixed."""
    a = _adapter()
    a.core.read_namespaced_service.return_value = _service()

    await a.provision_node(
        provider_resource_id="node-1", pool_id="p1",
        metadata=_metadata(cmd=["serve"]),
    )

    container = a.apps.create_namespaced_deployment.call_args.kwargs["body"]         .spec.template.spec.containers[0]

    assert container.args == ["serve"]
    assert container.command is None,         "setting command would discard the image entrypoint"


@pytest.mark.asyncio
async def test_explicit_command_overrides_the_entrypoint(monkeypatch):
    monkeypatch.delenv("K8S_SERVICE_TYPE", raising=False)
    a = _adapter()
    a.core.read_namespaced_service.return_value = _service()

    await a.provision_node(
        provider_resource_id="node-1", pool_id="p1",
        metadata=_metadata(command=["/bin/sh", "-c", "true"], cmd=None),
    )

    container = a.apps.create_namespaced_deployment.call_args.kwargs["body"]         .spec.template.spec.containers[0]

    assert container.command == ["/bin/sh", "-c", "true"]


@pytest.mark.asyncio
async def test_no_cmd_leaves_the_image_to_run_as_built(monkeypatch):
    monkeypatch.delenv("K8S_SERVICE_TYPE", raising=False)
    """The old default was ["sleep", "3600"], which as args to an engine image
    is nonsense. Omitting both is the correct default."""
    a = _adapter()
    a.core.read_namespaced_service.return_value = _service()

    md = _metadata()
    md.pop("cmd")
    await a.provision_node(
        provider_resource_id="node-1", pool_id="p1", metadata=md,
    )

    container = a.apps.create_namespaced_deployment.call_args.kwargs["body"]         .spec.template.spec.containers[0]

    assert container.command is None
    assert container.args is None


@pytest.mark.asyncio
async def test_deployment_and_service_share_a_selector(monkeypatch):
    monkeypatch.delenv("K8S_SERVICE_TYPE", raising=False)
    """A Service whose selector misses the pods routes to nothing."""
    a = _adapter()
    a.core.read_namespaced_service.return_value = _service()

    await a.provision_node(
        provider_resource_id="node-1", pool_id="p1", metadata=_metadata(),
    )

    dep = a.apps.create_namespaced_deployment.call_args.kwargs["body"]
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
async def test_failed_service_rolls_back_the_deployment():
    """Without a Service the workload has no address, so a half-created node
    must not be left behind reporting healthy."""
    a = _adapter()
    a.core.create_namespaced_service.side_effect = RuntimeError("boom")

    with pytest.raises(RuntimeError):
        await a.provision_node(
            provider_resource_id="node-1", pool_id="p1", metadata=_metadata(),
        )

    assert a.apps.delete_namespaced_deployment.called, \
        "Deployment must be removed when its Service could not be created"


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
    a.apps.read_namespaced_deployment.return_value = dep
    a.core.read_namespaced_service.return_value = _service(port=11434)

    url = await a.wait_for_ready(provider_instance_id="dep-1", timeout=5)

    assert url.startswith("http://"), "the router can only use an http address"
    assert "k8s://" not in url


@pytest.mark.asyncio
async def test_wait_for_ready_times_out_without_a_ready_replica():
    a = _adapter()
    dep = MagicMock()
    dep.status.ready_replicas = 0
    a.apps.read_namespaced_deployment.return_value = dep

    with pytest.raises(RuntimeError, match="no ready replica"):
        await a.wait_for_ready(provider_instance_id="dep-1", timeout=0)


@pytest.mark.asyncio
async def test_wait_for_ready_ignores_scheduled_but_unready_replicas():
    """A scheduled pod is not a serving one. Readiness must come from the
    probe, not from the pod existing."""
    a = _adapter()
    dep = MagicMock()
    dep.status.ready_replicas = None  # replicas exist, none ready
    a.apps.read_namespaced_deployment.return_value = dep

    with pytest.raises(RuntimeError):
        await a.wait_for_ready(provider_instance_id="dep-1", timeout=0)


# ---------------------------------------------------------------------------
# deprovision_node
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_deprovision_removes_both_objects():
    a = _adapter()

    await a.deprovision_node(provider_instance_id="dep-1")

    assert a.apps.delete_namespaced_deployment.called
    assert a.core.delete_namespaced_service.called, \
        "a leftover Service points at nothing"


@pytest.mark.asyncio
async def test_deprovision_tolerates_a_missing_deployment():
    """Deprovision runs on rollback paths too, where one object may be gone."""
    from kubernetes import client as k8s_client

    a = _adapter()
    a.apps.delete_namespaced_deployment.side_effect = \
        k8s_client.exceptions.ApiException(status=404)

    await a.deprovision_node(provider_instance_id="dep-1")

    assert a.core.delete_namespaced_service.called, \
        "the Service must still be removed when the Deployment is already gone"


@pytest.mark.asyncio
async def test_deprovision_reraises_a_real_api_error():
    from kubernetes import client as k8s_client

    a = _adapter()
    a.apps.delete_namespaced_deployment.side_effect = \
        k8s_client.exceptions.ApiException(status=403)

    with pytest.raises(k8s_client.exceptions.ApiException):
        await a.deprovision_node(provider_instance_id="dep-1")


# ---------------------------------------------------------------------------
# get_logs
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_get_logs_resolves_the_pod_by_label():
    """provider_instance_id names the Deployment. Pod names are generated, so
    reading logs by that name returns nothing."""
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

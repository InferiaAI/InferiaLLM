"""KubernetesAdapter's llm-d data-plane mode (session-3 T2, interfaces §5 v1).

A pool with metadata ``dataplane: "llm-d"`` is served through llm-d; every other
k8s pool keeps the StatefulSet path unchanged. The adapter's lifecycle calls
receive only the instance id, so llm-d instances are recognised by their
``inferia-llmd-`` prefix.
"""
import asyncio
from dataclasses import asdict
from unittest.mock import patch

import pytest

from orchestration.provisioning.engine.base import ProviderAdapter
from providers.k8s.k8s_adapter import KubernetesAdapter
from providers.k8s.llmd import resources as r

from .fake_k8s import fake_apis

NS = "inferia-llmd"


@pytest.fixture(autouse=True)
def _ns(monkeypatch):
    monkeypatch.setenv("K8S_LLMD_NAMESPACE", NS)
    monkeypatch.delenv("K8S_SERVICE_TYPE", raising=False)


def _adapter():
    with patch("providers.k8s.k8s_adapter.config"):
        a = KubernetesAdapter()
    c, a.core, a.apps, a.custom = fake_apis()
    a.llmd_poll_seconds = 0.001
    return c, a


def _md(**over):
    md = {"dataplane": "llm-d", "engine": r.SIM_ENGINE, "model_id": "sim-model", "replicas": 2}
    md.update(over)
    return md


def run(coro):
    return asyncio.run(coro)


def _provision(a, md=None):
    return run(a.provision_node(provider_resource_id="cpu", pool_id="pool-1", metadata=md or _md()))


def _mark_ready(c, name):
    c.set_status("Deployment", NS, r.model_server_name(name), ready_replicas=1)
    c.set_status("Deployment", NS, r.epp_name(name), available_replicas=1)


# --- capabilities ------------------------------------------------------------
def test_base_hook_defaults_to_class_capabilities():
    class A(KubernetesAdapter):
        pass
    assert ProviderAdapter.capabilities_for(A, {"x": 1}) is A.CAPABILITIES


def test_llmd_capabilities_require_a_served_probe():
    _, a = _adapter()
    caps = a.capabilities_for({"dataplane": "llm-d"})
    assert caps.endpoint_http_probeable is True
    assert caps.features["dataplane"] == "llm-d"
    assert caps.features["kv_aware_routing"] is True


def test_plain_k8s_capabilities_are_unchanged():
    _, a = _adapter()
    assert a.capabilities_for({}) is KubernetesAdapter.CAPABILITIES
    assert a.capabilities_for(None) is KubernetesAdapter.CAPABILITIES
    assert KubernetesAdapter.CAPABILITIES.endpoint_http_probeable is False


def test_llmd_capabilities_do_not_mutate_the_class():
    _, a = _adapter()
    before = asdict(KubernetesAdapter.CAPABILITIES)
    a.capabilities_for({"dataplane": "llm-d"})
    assert asdict(KubernetesAdapter.CAPABILITIES) == before


# --- provision ---------------------------------------------------------------
def test_provision_creates_llmd_objects_and_returns_the_gateway():
    c, a = _adapter()
    spec = _provision(a)
    pid = spec["provider_instance_id"]
    assert r.is_llmd_instance(pid)
    assert spec["expose_url"] == f"http://{pid}.{NS}.svc.cluster.local:{r.PROXY_PORT}"
    assert spec["metadata"]["dataplane"] == "llm-d"
    assert spec["metadata"]["namespace"] == NS
    assert ("InferencePool", NS, pid) in c.objects
    assert not any(k == "StatefulSet" for k, _, _ in c.objects)


def test_provision_nodeport_url_reaches_an_out_of_cluster_control_plane(monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setenv("K8S_SERVICE_TYPE", "NodePort")
    c, a = _adapter()
    node = SimpleNamespace(status=SimpleNamespace(addresses=[SimpleNamespace(type="InternalIP", address="172.18.0.2")]))
    a.core.list_node = lambda: SimpleNamespace(items=[node])
    spec = _provision(a)
    assert spec["expose_url"] == "http://172.18.0.2:30001"


def test_provision_refuses_an_unsupported_engine_without_creating_anything():
    c, a = _adapter()
    with pytest.raises(r.LlmdSpecError):
        _provision(a, _md(engine="vllm"))
    assert c.objects == {}


def test_plain_pool_still_gets_a_statefulset():
    from unittest.mock import MagicMock
    with patch("providers.k8s.k8s_adapter.config"):
        a = KubernetesAdapter()
    a.core, a.apps, a.custom = MagicMock(), MagicMock(), MagicMock()
    a.core.read_namespaced_service.return_value.spec.type = "ClusterIP"
    a.core.read_namespaced_service.return_value.spec.ports = [MagicMock(port=11434)]
    spec = run(a.provision_node(provider_resource_id="x", pool_id="p",
                                metadata={"engine": "ollama", "model_id": "m"}))
    assert spec["provider_instance_id"].startswith("inferia-worker-")
    a.apps.create_namespaced_stateful_set.assert_called_once()


# --- readiness ---------------------------------------------------------------
def test_wait_for_ready_returns_the_gateway_once_ready():
    c, a = _adapter()
    pid = _provision(a)["provider_instance_id"]
    _mark_ready(c, pid)
    url = run(a.wait_for_ready(provider_instance_id=pid, timeout=5))
    assert url == f"http://{pid}.{NS}.svc.cluster.local:{r.PROXY_PORT}"


def test_wait_for_ready_times_out_when_never_ready():
    _, a = _adapter()
    pid = _provision(a)["provider_instance_id"]
    with pytest.raises(RuntimeError, match="not ready"):
        run(a.wait_for_ready(provider_instance_id=pid, timeout=0.05))


def test_wait_for_ready_fails_fast_on_a_bad_image():
    c, a = _adapter()
    pid = _provision(a)["provider_instance_id"]
    c.add_pod(NS, {r.INSTANCE_LABEL: pid}, waiting_reason="ImagePullBackOff")
    with pytest.raises(RuntimeError, match="ImagePullBackOff"):
        run(a.wait_for_ready(provider_instance_id=pid, timeout=60))


# --- node status (liveness worker) --------------------------------------------
def test_node_status_running_when_ready():
    c, a = _adapter()
    pid = _provision(a)["provider_instance_id"]
    _mark_ready(c, pid)
    assert run(a.get_node_status(provider_instance_id=pid)) == "RUNNING"


def test_node_status_stopped_only_when_confirmed_gone():
    _, a = _adapter()
    assert run(a.get_node_status(provider_instance_id="inferia-llmd-gone00")) == "STOPPED"


def test_node_status_unknown_while_pending_or_on_error():
    c, a = _adapter()
    pid = _provision(a)["provider_instance_id"]
    assert run(a.get_node_status(provider_instance_id=pid)) == "unknown"

    def boom(**_):
        raise RuntimeError("api down")
    a.apps.read_namespaced_deployment = boom
    assert run(a.get_node_status(provider_instance_id=pid)) == "unknown"


def test_plain_k8s_node_status_stays_unknown():
    _, a = _adapter()
    assert run(a.get_node_status(provider_instance_id="inferia-worker-abc123")) == "unknown"


# --- delete ------------------------------------------------------------------
def test_deprovision_removes_everything_and_is_idempotent():
    c, a = _adapter()
    pid = _provision(a)["provider_instance_id"]
    run(a.deprovision_node(provider_instance_id=pid))
    assert c.objects == {}
    run(a.deprovision_node(provider_instance_id=pid))


# --- logs --------------------------------------------------------------------
def test_log_streaming_points_at_the_llmd_namespace():
    _, a = _adapter()
    info = run(a.get_log_streaming_info(provider_instance_id="inferia-llmd-abc123"))
    assert info["subscription"]["namespace"] == NS
    assert info["subscription"]["instance"] == "inferia-llmd-abc123"


def test_plain_log_streaming_keeps_default_namespace():
    _, a = _adapter()
    info = run(a.get_log_streaming_info(provider_instance_id="inferia-worker-abc123"))
    assert info["subscription"]["namespace"] == "default"

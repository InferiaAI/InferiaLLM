"""Direct provisioning drives an llm-d pool deployment to RUNNING (session-3 T2).

The real KubernetesAdapter runs over the in-memory cluster (fake_k8s.py); only
the HTTP readiness probe and the repositories are mocked. Pins:
  * the pool, and only the pool, selects the data plane;
  * an llm-d deployment reaches RUNNING only after the probe through the
    router succeeds (PENDING_NODE -> DEPLOYING -> RUNNING);
  * plain k8s pools keep "RUNNING once scheduled".
"""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from orchestration.models.model_deployment import direct_provision
from orchestration.repositories.inventory_repo import InventoryRepository
from orchestration.repositories.model_deployment_repo import ModelDeploymentRepository
from providers.k8s.k8s_adapter import KubernetesAdapter
from providers.k8s.llmd import resources as r

from .fake_k8s import fake_apis

NS = "inferia-llmd"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("K8S_LLMD_NAMESPACE", NS)
    monkeypatch.delenv("K8S_SERVICE_TYPE", raising=False)


def _adapter():
    with patch("providers.k8s.k8s_adapter.config"):
        a = KubernetesAdapter()
    c, a.core, a.apps, a.custom = fake_apis()
    a.llmd_poll_seconds = 0.001
    return c, a


def _deps(configuration):
    deploys = AsyncMock(spec=ModelDeploymentRepository)
    deploys.get.return_value = {
        "state": "PENDING_NODE",
        "configuration": configuration,
        "inference_model": "sim-model",
        "model_name": "sim-model",
        "engine": r.SIM_ENGINE,
        "deployment_id": "d-1",
        "gpu_per_replica": 0,
    }
    deploys.update_state_if.return_value = True
    inventory = AsyncMock(spec=InventoryRepository)
    inventory.finalize_direct_node.return_value = True
    return SimpleNamespace(deploys=deploys, inventory=inventory)


def _provision(adapter, deps, pool_meta, probe="ready"):
    pool_row = {"allowed_gpu_types": ["cpu"], "provider_pool_id": "pool-1",
                "provider_credential_name": None}

    async def go():
        with patch.object(direct_provision, "get_adapter", return_value=adapter), \
             patch.object(direct_provision, "_wait_endpoint_serving",
                          AsyncMock(return_value=probe)) as wait:
            await direct_provision.provision_direct_node(
                deploy_id="d-1", node_id="n-1", pool_row=pool_row, pool_meta=pool_meta,
                provider="k8s", gpu_per_replica=0, deps=deps,
            )
            return wait
    return asyncio.run(go())


def _ready_all(c):
    for kind, ns, name in list(c.objects):
        if kind == "Deployment":
            c.set_status(kind, ns, name, ready_replicas=1, available_replicas=1)


class _ReadyOnCreate:
    """Marks Deployments ready as they are created, like a healthy cluster."""
    def __init__(self, c):
        self.c = c
        orig = c._create

        def create(kind, ns, body):
            out = orig(kind, ns, body)
            if kind == "Deployment":
                c.set_status(kind, ns, body["metadata"]["name"], ready_replicas=1, available_replicas=1)
            return out
        c._create = create


def test_pool_dataplane_selects_llmd_and_gates_running_on_the_probe():
    c, a = _adapter()
    _ReadyOnCreate(c)
    deps = _deps({"engine": r.SIM_ENGINE, "model_id": "sim-model", "replicas": 2})
    wait = _provision(a, deps, {"dataplane": "llm-d"})

    pools = [n for k, _, n in c.objects if k == "InferencePool"]
    assert len(pools) == 1 and r.is_llmd_instance(pools[0])
    gw = f"http://{pools[0]}.{NS}.svc.cluster.local:{r.PROXY_PORT}"
    wait.assert_awaited_once()
    assert wait.await_args.kwargs["expose_url"] == gw
    deps.deploys.update_endpoint.assert_awaited_once()
    assert deps.deploys.update_endpoint.await_args.args[1] == gw
    transitions = [call.args[1:] for call in deps.deploys.update_state_if.await_args_list]
    assert transitions == [("PENDING_NODE", "DEPLOYING"), ("DEPLOYING", "RUNNING")]
    deps.deploys.update_state.assert_not_awaited()  # never the unconditional RUNNING


def test_probe_timeout_fails_and_cleans_up():
    c, a = _adapter()
    _ReadyOnCreate(c)
    deps = _deps({"engine": r.SIM_ENGINE, "model_id": "sim-model"})
    _provision(a, deps, {"dataplane": "llm-d"}, probe="timeout")
    deps.deploys.update_state.assert_awaited()
    assert deps.deploys.update_state.await_args.args[1] == "FAILED"
    assert c.objects == {}  # deprovisioned, verified gone


def test_unsupported_engine_fails_without_creating_anything():
    c, a = _adapter()
    deps = _deps({"engine": "vllm", "model_id": "m"})
    deps.deploys.get.return_value["engine"] = "vllm"
    _provision(a, deps, {"dataplane": "llm-d"})
    assert deps.deploys.update_state.await_args.args[1] == "FAILED"
    assert "not supported" in deps.deploys.update_state.await_args.kwargs["error_message"]
    assert c.objects == {}


def test_a_deployment_cannot_pick_the_dataplane_itself():
    # Only the pool decides; a deployment's own configuration key is dropped.
    from unittest.mock import MagicMock
    _, a = _adapter()
    a.provision_node = AsyncMock(side_effect=RuntimeError("stop"))
    deps = _deps({"engine": r.SIM_ENGINE, "model_id": "m", "dataplane": "llm-d"})
    _provision(a, deps, {})
    md = a.provision_node.await_args.kwargs["metadata"]
    assert "dataplane" not in md
    assert MagicMock  # keep import used


def test_pool_dataplane_overrides_a_deployment_value():
    _, a = _adapter()
    a.provision_node = AsyncMock(side_effect=RuntimeError("stop"))
    deps = _deps({"engine": r.SIM_ENGINE, "model_id": "m", "dataplane": "something-else"})
    _provision(a, deps, {"dataplane": "llm-d"})
    assert a.provision_node.await_args.kwargs["metadata"]["dataplane"] == "llm-d"


def test_sim_engine_skips_the_model_mirror_when_egress_is_off():
    _, a = _adapter()
    a.provision_node = AsyncMock(side_effect=RuntimeError("stop"))
    deps = _deps({"engine": r.SIM_ENGINE, "model_id": "sim-model"})
    with patch.object(direct_provision._mc_deps, "egress_allowed", return_value=False), \
         patch.object(direct_provision, "resolve_and_apply_mirror", AsyncMock()) as mirror:
        _provision(a, deps, {"dataplane": "llm-d"})
    mirror.assert_not_awaited()


def test_plain_k8s_pool_keeps_running_once_scheduled():
    # Unchanged behaviour: no probe, unconditional RUNNING.
    _, a = _adapter()
    a.provision_node = AsyncMock(return_value={
        "provider_instance_id": "inferia-worker-abc123", "hostname": "h",
        "expose_url": "http://inferia-worker-abc123.default.svc.cluster.local:11434",
        "metadata": {},
    })
    a.wait_for_ready = AsyncMock(return_value="http://inferia-worker-abc123.default.svc.cluster.local:11434")
    deps = _deps({"engine": "ollama", "model_id": "m"})
    wait = _provision(a, deps, {})
    wait.assert_not_awaited()
    deps.deploys.update_state.assert_awaited_with("d-1", "RUNNING")

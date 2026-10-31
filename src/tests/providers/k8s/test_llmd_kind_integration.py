"""The llm-d data-plane mode against a real kind cluster (session-3 T2).

Skipped unless INFERIA_KIND_CONTEXT names a kubeconfig context (CI has no
cluster). Bring one up with deploy/kind/up.sh, then:

    INFERIA_KIND_CONTEXT=kind-inferia-s3 K8S_SERVICE_TYPE=NodePort \
        .venv/bin/python -m pytest src/tests/providers/k8s/test_llmd_kind_integration.py -v

NodePort makes the router reachable from the host through the kind node's IP,
the way an out-of-cluster control plane reaches it.
"""
import asyncio
import json
import os
import urllib.request
from unittest.mock import patch

import pytest

CONTEXT = os.environ.get("INFERIA_KIND_CONTEXT")
pytestmark = pytest.mark.skipif(not CONTEXT, reason="needs INFERIA_KIND_CONTEXT (a kind cluster)")


def _adapter():
    from kubernetes import client, config
    from providers.k8s.k8s_adapter import KubernetesAdapter

    config.load_kube_config(context=CONTEXT)
    with patch("providers.k8s.k8s_adapter.config"):
        a = KubernetesAdapter()
    a.core, a.apps, a.custom = client.CoreV1Api(), client.AppsV1Api(), client.CustomObjectsApi()
    a.llmd_poll_seconds = 2
    return a


def _complete(url, prompt):
    req = urllib.request.Request(
        f"{url}/v1/completions",
        data=json.dumps({"model": "sim-model", "prompt": prompt, "max_tokens": 4}).encode(),
        headers={"Content-Type": "application/json", "Connection": "close"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.status, resp.headers.get("x-inference-pod"), json.loads(resp.read())


def test_provision_serve_route_and_delete():
    from providers.k8s.llmd import resources as r

    a = _adapter()
    md = {"dataplane": "llm-d", "engine": r.SIM_ENGINE, "model_id": "sim-model", "replicas": 2}

    async def go():
        spec = await a.provision_node(provider_resource_id="cpu", pool_id="it", metadata=md)
        pid = spec["provider_instance_id"]
        try:
            url = await a.wait_for_ready(provider_instance_id=pid, timeout=300)
            assert url == spec["expose_url"]
            assert await a.get_node_status(provider_instance_id=pid) == "RUNNING"

            # Served through Envoy + the EPP; retry while the EPP subscribes.
            prefix = "shared prefix " * 400
            pods = []
            for i in range(8):
                for _ in range(30):
                    try:
                        status, pod, body = await asyncio.to_thread(_complete, url, f"{prefix} q{i}")
                        break
                    except Exception:
                        await asyncio.sleep(2)
                else:
                    pytest.fail("router never served a completion")
                assert status == 200 and body["choices"]
                pods.append(pod)
                await asyncio.sleep(0.5)
            # Same long prefix every time: prefix-aware routing keeps it on one pod
            # after the first request has populated that pod's cache.
            assert len(set(pods[1:])) == 1, pods
        finally:
            await a.deprovision_node(provider_instance_id=pid)
        assert await a.get_node_status(provider_instance_id=pid) == "STOPPED"
        left = a.apps.list_namespaced_deployment(
            namespace=r.llmd_namespace(), label_selector=f"{r.INSTANCE_LABEL}={pid}").items
        assert left == []

    asyncio.run(go())

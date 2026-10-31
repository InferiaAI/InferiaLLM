"""An in-memory stand-in for the three kubernetes client APIs the llm-d mode uses.

Stores bodies by (kind, namespace, name) and raises the client's real
ApiException with 404/409, so code under test sees the same errors a cluster
returns. Status fields are set by tests through ``set_status``.
"""
from types import SimpleNamespace

from kubernetes.client.exceptions import ApiException


class FakeCluster:
    def __init__(self):
        self.objects = {}            # (kind, ns, name) -> body
        self.status = {}             # (kind, ns, name) -> dict
        self.pods = {}               # ns -> list of pod SimpleNamespace
        self.fail_create = set()     # kinds whose create raises 500
        self.sticky_delete = set()   # (kind, ns, name) that survive delete
        self.slow_delete = {}        # (kind, ns, name) -> reads it stays visible after delete
        self.terminating = {}
        self.calls = []

    # helpers ---------------------------------------------------------------
    def _create(self, kind, namespace, body):
        self.calls.append(("create", kind, body["metadata"]["name"]))
        if kind in self.fail_create:
            raise ApiException(status=500, reason="boom")
        key = (kind, namespace, body["metadata"]["name"])
        if key in self.objects:
            raise ApiException(status=409, reason="AlreadyExists")
        self.objects[key] = body
        return body

    def _read(self, kind, namespace, name):
        key = (kind, namespace, name)
        if key in self.terminating:
            # Deletion in progress: visible for a few more reads, like an
            # object waiting on its finalizers or its pods.
            self.terminating[key] -= 1
            if self.terminating[key] < 0:
                del self.terminating[key]
                self.objects.pop(key, None)
        if key not in self.objects:
            raise ApiException(status=404, reason="NotFound")
        st = self.status.get(key, {})
        body = self.objects[key]
        spec = None
        if kind == "Service":
            s = body["spec"]
            spec = SimpleNamespace(
                type=s.get("type", "ClusterIP"),
                ports=[SimpleNamespace(port=p["port"], node_port=30001) for p in s["ports"]],
            )
        return SimpleNamespace(
            metadata=SimpleNamespace(name=name),
            spec=spec,
            status=SimpleNamespace(
                ready_replicas=st.get("ready_replicas"),
                available_replicas=st.get("available_replicas"),
            ),
        )

    def _delete(self, kind, namespace, name):
        self.calls.append(("delete", kind, name))
        key = (kind, namespace, name)
        if key not in self.objects:
            raise ApiException(status=404, reason="NotFound")
        if key in self.slow_delete:
            self.terminating[key] = self.slow_delete[key]
        elif key not in self.sticky_delete:
            del self.objects[key]

    def set_status(self, kind, namespace, name, **st):
        self.status[(kind, namespace, name)] = st

    def kinds(self):
        return sorted({k for k, _, _ in self.objects})

    def add_pod(self, namespace, labels, waiting_reason=None, message=""):
        waiting = SimpleNamespace(reason=waiting_reason, message=message) if waiting_reason else None
        cs = SimpleNamespace(state=SimpleNamespace(waiting=waiting))
        self.pods.setdefault(namespace, []).append(SimpleNamespace(
            metadata=SimpleNamespace(name=f"pod-{len(self.pods.get(namespace, []))}", labels=labels),
            status=SimpleNamespace(container_statuses=[cs]),
        ))


class FakeCore:
    def __init__(self, c: FakeCluster):
        self.c = c

    def create_namespaced_config_map(self, namespace, body):
        return self.c._create("ConfigMap", namespace, body)

    def create_namespaced_service(self, namespace, body):
        return self.c._create("Service", namespace, body)

    def read_namespaced_config_map(self, name, namespace):
        return self.c._read("ConfigMap", namespace, name)

    def read_namespaced_service(self, name, namespace):
        return self.c._read("Service", namespace, name)

    def delete_namespaced_config_map(self, name, namespace):
        return self.c._delete("ConfigMap", namespace, name)

    def delete_namespaced_service(self, name, namespace):
        return self.c._delete("Service", namespace, name)

    def list_namespaced_pod(self, namespace, label_selector):
        k, v = label_selector.split("=", 1)
        items = [p for p in self.c.pods.get(namespace, []) if p.metadata.labels.get(k) == v]
        return SimpleNamespace(items=items)


class FakeApps:
    def __init__(self, c: FakeCluster):
        self.c = c

    def create_namespaced_deployment(self, namespace, body):
        return self.c._create("Deployment", namespace, body)

    def read_namespaced_deployment(self, name, namespace):
        return self.c._read("Deployment", namespace, name)

    def delete_namespaced_deployment(self, name, namespace):
        return self.c._delete("Deployment", namespace, name)


class FakeCustom:
    def __init__(self, c: FakeCluster):
        self.c = c

    def create_namespaced_custom_object(self, group, version, namespace, plural, body):
        assert (group, version, plural) == ("inference.networking.k8s.io", "v1", "inferencepools")
        return self.c._create(body["kind"], namespace, body)

    def get_namespaced_custom_object(self, group, version, namespace, plural, name):
        return self.c._read("InferencePool", namespace, name)

    def delete_namespaced_custom_object(self, group, version, namespace, plural, name):
        return self.c._delete("InferencePool", namespace, name)


def fake_apis():
    c = FakeCluster()
    return c, FakeCore(c), FakeApps(c), FakeCustom(c)

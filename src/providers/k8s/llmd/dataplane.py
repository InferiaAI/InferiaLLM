"""Apply, watch and delete one deployment's llm-d objects.

The kubernetes client is synchronous, so every call runs in the default
executor, as the rest of the Kubernetes adapter does.
"""
from __future__ import annotations

import asyncio
import functools
import logging
import time
from typing import Dict, List, Optional, Tuple

from kubernetes.client.exceptions import ApiException

from providers.k8s.llmd import resources as r

log = logging.getLogger(__name__)

_POOL_API = dict(group="inference.networking.k8s.io", version="v1", plural="inferencepools")

# Pulls that will not succeed on their own (same set as the adapter's).
_PULL_FAILED_REASONS = {
    "ErrImagePull", "ImagePullBackOff", "InvalidImageName",
    "ImageInspectError", "RegistryUnavailable",
}


async def _run(func, *args, **kwargs):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, functools.partial(func, *args, **kwargs))


class LlmdDataplane:
    def __init__(self, *, core, apps, custom, poll_seconds: float = 1.0):
        self.core = core
        self.apps = apps
        self.custom = custom
        self.poll_seconds = poll_seconds

    # --- per-kind dispatch ---------------------------------------------------
    def _create_fn(self, kind: str, namespace: str, body: Dict):
        if kind == "ConfigMap":
            return functools.partial(self.core.create_namespaced_config_map, namespace=namespace, body=body)
        if kind == "Service":
            return functools.partial(self.core.create_namespaced_service, namespace=namespace, body=body)
        if kind == "Deployment":
            return functools.partial(self.apps.create_namespaced_deployment, namespace=namespace, body=body)
        if kind == "InferencePool":
            return functools.partial(self.custom.create_namespaced_custom_object,
                                     namespace=namespace, body=body, **_POOL_API)
        raise ValueError(f"unexpected kind {kind}")

    def _read_fn(self, kind: str, namespace: str, name: str):
        if kind == "ConfigMap":
            return functools.partial(self.core.read_namespaced_config_map, name=name, namespace=namespace)
        if kind == "Service":
            return functools.partial(self.core.read_namespaced_service, name=name, namespace=namespace)
        if kind == "Deployment":
            return functools.partial(self.apps.read_namespaced_deployment, name=name, namespace=namespace)
        if kind == "InferencePool":
            return functools.partial(self.custom.get_namespaced_custom_object,
                                     namespace=namespace, name=name, **_POOL_API)
        raise ValueError(f"unexpected kind {kind}")

    def _delete_fn(self, kind: str, namespace: str, name: str):
        if kind == "ConfigMap":
            return functools.partial(self.core.delete_namespaced_config_map, name=name, namespace=namespace)
        if kind == "Service":
            return functools.partial(self.core.delete_namespaced_service, name=name, namespace=namespace)
        if kind == "Deployment":
            return functools.partial(self.apps.delete_namespaced_deployment, name=name, namespace=namespace)
        if kind == "InferencePool":
            return functools.partial(self.custom.delete_namespaced_custom_object,
                                     namespace=namespace, name=name, **_POOL_API)
        raise ValueError(f"unexpected kind {kind}")

    @staticmethod
    def _inventory(name: str) -> List[Tuple[str, str]]:
        """(kind, name) of everything an instance owns, entry point first, so
        delete stops new traffic before removing what serves it."""
        return [
            ("Service", r.gateway_service_name(name)),
            ("Deployment", r.epp_name(name)),
            ("Service", r.epp_service_name(name)),
            ("InferencePool", name),
            ("Deployment", r.model_server_name(name)),
            ("Service", r.fallback_service_name(name)),
            ("ConfigMap", r.envoy_config_name(name)),
            ("ConfigMap", r.epp_config_name(name)),
        ]

    # --- apply ---------------------------------------------------------------
    async def apply(self, spec: r.LlmdSpec) -> None:
        """Create every object. On any failure, delete what this call created
        (and only that) and re-raise.

        Create, not apply-or-update: names are random per deployment, so an
        existing object belongs to something else and must not be adopted.
        """
        created: List[Tuple[str, str]] = []
        try:
            for body in r.build_objects(spec):
                kind, name = body["kind"], body["metadata"]["name"]
                await _run(self._create_fn(kind, spec.namespace, body))
                created.append((kind, name))
        except Exception:
            log.exception("llm-d: create failed for %s; rolling back %d objects", spec.name, len(created))
            for kind, name in reversed(created):
                try:
                    await _run(self._delete_fn(kind, spec.namespace, name))
                except ApiException as e:
                    if e.status != 404:
                        log.warning("llm-d: rollback of %s/%s failed: %s", kind, name, e)
            raise

    # --- status --------------------------------------------------------------
    async def status(self, name: str, namespace: str) -> Tuple[str, str]:
        """("ready" | "pending" | "failed" | "missing", detail).

        Ready means a model server is Ready *and* the router is available. It
        is the precondition for the HTTP probe that gates RUNNING, not a
        substitute for it.
        """
        try:
            model = await _run(self._read_fn("Deployment", namespace, r.model_server_name(name)))
        except ApiException as e:
            if e.status == 404:
                return "missing", ""
            raise

        pulled = await self._pull_failure(name, namespace)
        if pulled:
            return "failed", pulled

        if (model.status.ready_replicas or 0) < 1:
            return "pending", "no model server ready"
        try:
            router = await _run(self._read_fn("Deployment", namespace, r.epp_name(name)))
        except ApiException as e:
            if e.status == 404:
                return "pending", "router missing"
            raise
        if (router.status.available_replicas or 0) < 1:
            return "pending", "router not available"
        return "ready", ""

    async def _pull_failure(self, name: str, namespace: str) -> Optional[str]:
        try:
            pods = await _run(self.core.list_namespaced_pod, namespace=namespace,
                              label_selector=f"{r.INSTANCE_LABEL}={name}")
        except Exception:  # noqa: BLE001 — unreadable pods are not a failure
            return None
        for pod in pods.items:
            for cs in pod.status.container_statuses or []:
                waiting = getattr(cs.state, "waiting", None)
                if waiting is not None and waiting.reason in _PULL_FAILED_REASONS:
                    return f"{pod.metadata.name}: {waiting.reason}: {waiting.message or ''}".rstrip(": ")
        return None

    # --- delete --------------------------------------------------------------
    async def delete(self, name: str, namespace: str, verify_timeout: float = 120.0) -> None:
        """Delete everything the instance owns, then wait until it is gone.

        Safe to retry: missing objects are the outcome we want. Raises if
        anything is still there after ``verify_timeout``, naming it.
        """
        inventory = self._inventory(name)
        for kind, obj in inventory:
            try:
                await _run(self._delete_fn(kind, namespace, obj))
            except ApiException as e:
                if e.status != 404:
                    raise

        deadline = time.monotonic() + verify_timeout
        while True:
            left = []
            for kind, obj in inventory:
                try:
                    await _run(self._read_fn(kind, namespace, obj))
                    left.append(f"{kind}/{obj}")
                except ApiException as e:
                    if e.status != 404:
                        raise
            if not left:
                return
            if time.monotonic() >= deadline:
                raise RuntimeError(f"llm-d: still present after delete in {namespace}: {', '.join(left)}")
            await asyncio.sleep(self.poll_seconds)

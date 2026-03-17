from kubernetes import client, config
from kubernetes.client.exceptions import ApiException
import asyncio
import functools


async def _run_sync(func, *args, **kwargs):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, functools.partial(func, *args, **kwargs))


class LLMdK8sClient:
    def __init__(self, namespace="default"):
        config.load_kube_config()
        self.api = client.CustomObjectsApi()
        self.namespace = namespace

    async def apply(self, spec: dict):
        name = spec["metadata"]["name"]

        try:
            return await _run_sync(
                self.api.create_namespaced_custom_object,
                group="llmd.ai",
                version="v1",
                namespace=self.namespace,
                plural="llmdeployments",
                body=spec,
            )
        except ApiException as e:
            if e.status == 409:
                return await _run_sync(
                    self.api.patch_namespaced_custom_object,
                    group="llmd.ai",
                    version="v1",
                    namespace=self.namespace,
                    plural="llmdeployments",
                    name=name,
                    body=spec,
                )
            raise

    async def delete(self, name: str):
        await _run_sync(
            self.api.delete_namespaced_custom_object,
            group="llmd.ai",
            version="v1",
            namespace=self.namespace,
            plural="llmdeployments",
            name=name,
        )

    async def get_status(self, name: str):
        obj = await _run_sync(
            self.api.get_namespaced_custom_object,
            group="llmd.ai",
            version="v1",
            namespace=self.namespace,
            plural="llmdeployments",
            name=name,
        )
        return obj.get("status", {})

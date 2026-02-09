from kubernetes import client, config
import logging

logger = logging.getLogger("llmd-client")


class LLMdK8sClient:
    def __init__(self, namespace: str = "default"):
        config.load_kube_config()
        self.namespace = namespace
        self.api = client.CustomObjectsApi()

    async def apply(self, spec: dict):
        name = spec["metadata"]["name"]

        try:
            self.api.create_namespaced_custom_object(
                group="llmd.ai",
                version="v1",
                namespace=self.namespace,
                plural="llmddeployments",
                body=spec,
            )
            logger.info("llm-d CRD created: %s", name)
        except client.exceptions.ApiException as e:
            if e.status == 409:
                self.api.replace_namespaced_custom_object(
                    group="llmd.ai",
                    version="v1",
                    namespace=self.namespace,
                    plural="llmddeployments",
                    name=name,
                    body=spec,
                )
                logger.info("llm-d CRD updated: %s", name)
            else:
                raise

    def get(self, name: str):
        return self.api.get_namespaced_custom_object(
            group="llmd.ai",
            version="v1",
            namespace=self.namespace,
            plural="llmddeployments",
            name=name,
        )

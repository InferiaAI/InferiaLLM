import random
from uuid import UUID

from services.vllm_runtime.process import VLLMProcess
from services.vllm_runtime.health import wait_until_ready


class VLLMRuntime:
    def __init__(self):
        self.processes = {}

    async def deploy(
        self,
        *,
        deployment_id: UUID,
        model,
        node_id: UUID,
        gpu_id: int = 0,
    ):
        port = random.randint(20000, 30000)
        endpoint = f"http://{node_id}:{port}"

        proc = VLLMProcess(
            model_path=model["artifact_path"],
            port=port,
            gpu_id=gpu_id,
        )

        proc.start()

        try:
            await wait_until_ready(endpoint)
        except Exception:
            proc.stop()
            raise

        self.processes[deployment_id] = proc

        return {
            "node_ids": [node_id],
            "allocation_ids": None,   # vLLM is node-local
            "llmd_resource_name": None,
        }

    async def stop(self, deployment_id: UUID):
        proc = self.processes.pop(deployment_id, None)
        if proc:
            proc.stop()

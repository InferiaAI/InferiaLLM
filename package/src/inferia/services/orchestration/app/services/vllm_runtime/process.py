import asyncio
import os
import signal
import subprocess
from typing import Optional


class VLLMProcess:
    def __init__(
        self,
        *,
        model_path: str,
        port: int,
        gpu_id: int = 0,
        tensor_parallel: int = 1,
    ):
        self.model_path = model_path
        self.port = port
        self.gpu_id = gpu_id
        self.tensor_parallel = tensor_parallel
        self.proc: Optional[subprocess.Popen] = None

    def start(self):
        if self.proc:
            raise RuntimeError("vLLM process already running")

        env = os.environ.copy()
        env["CUDA_VISIBLE_DEVICES"] = str(self.gpu_id)

        cmd = [
            "python",
            "-m",
            "vllm.entrypoints.openai.api_server",
            "--model", self.model_path,
            "--port", str(self.port),
            "--tensor-parallel-size", str(self.tensor_parallel),
        ]

        self.proc = subprocess.Popen(
            cmd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def is_running(self) -> bool:
        return self.proc and self.proc.poll() is None

    def stop(self):
        if not self.proc:
            return

        self.proc.send_signal(signal.SIGTERM)
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()

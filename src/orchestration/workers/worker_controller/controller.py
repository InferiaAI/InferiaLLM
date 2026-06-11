"""
WorkerController — the facade other services call.

This object is wired into model_deployment: when a deployment of kind='worker'
needs to start/stop, the deployment worker calls
``controller.load_model(node_id, spec)`` instead of the old
``llmd_runtime.deploy(...)`` path.
"""

from __future__ import annotations

import asyncio
import uuid

from orchestration.messaging.uri_validation import (
    sanitize_config,
    validate_artifact_uri,
)

from .protocol import (
    CommandResultBody,
    Envelope,
    LoadModelBody,
    ModelRef,
    UnloadModelBody,
)
from .registry import WorkerRegistry


class NodeUnreachableError(Exception):
    """Raised when the caller targets a node that has no live WS connection."""


_DEFAULT_TIMEOUT = 2400.0  # seconds — must exceed the worker's pull budget
# (PULL_TIMEOUT_SECONDS, default 1800s) plus container start + readiness.
# Large vLLM images (e.g. vllm-openai:v0.22.x ≈ 35 GiB uncompressed) take well
# over 10 min to pull+extract on a gp3 root volume; at the old 900s the CP timed
# out the load_model command before the worker finished pulling.


class WorkerController:
    def __init__(
        self,
        registry: WorkerRegistry,
        *,
        command_timeout: float = _DEFAULT_TIMEOUT,
    ):
        self.registry = registry
        self.command_timeout = command_timeout

    async def load_model(self, node_id: str, spec: dict) -> CommandResultBody:
        """Issue LoadModel to the worker at node_id and await its reply.

        Returns the CommandResultBody verbatim. The caller (model_deployment)
        decides what to do with status=='failed' (mark deployment failed,
        scheduler may re-place, etc.).

        Raises NodeUnreachableError if the worker isn't connected, ValueError
        if the spec fails CP-side validation, asyncio.TimeoutError if the
        worker doesn't reply within command_timeout.
        """
        # Validate spec first — cheap fail-fast, also testable without a
        # connected node.
        validate_artifact_uri(spec["model"]["artifact_uri"])
        clean_config = sanitize_config(spec.get("config"))

        if self.registry.get(node_id) is None:
            raise NodeUnreachableError(f"node {node_id} not connected")

        import logging as _logging
        _log = _logging.getLogger("orchestration.workers.worker_controller")
        _log.info(
            "load_model: sending node=%s deployment=%s recipe=%s uri=%s",
            node_id, spec.get("deployment_id"), spec.get("recipe"),
            spec.get("model", {}).get("artifact_uri"),
        )

        body = LoadModelBody(
            deployment_id=spec["deployment_id"],
            recipe=spec["recipe"],
            model=ModelRef(
                artifact_uri=spec["model"]["artifact_uri"],
                format=spec["model"].get("format", ""),
                backend=spec["model"].get("backend", ""),
            ),
            config=clean_config,
            gpu_indices=list(spec.get("gpu_indices", [])),
            port=int(spec.get("port", 0)),
            env=dict(spec.get("env", {})),
        )
        env = Envelope(
            type="LoadModel",
            id=str(uuid.uuid4()),
            body=body.model_dump(),
        )
        fut = self.registry.expect_command_result(env.id, timeout=self.command_timeout)
        ok = await self.registry.send(node_id, env)
        if not ok:
            raise NodeUnreachableError(f"failed to send to {node_id}")
        return await fut

    async def unload_model(self, node_id: str, deployment_id: str) -> CommandResultBody:
        if self.registry.get(node_id) is None:
            raise NodeUnreachableError(f"node {node_id} not connected")
        body = UnloadModelBody(deployment_id=deployment_id)
        env = Envelope(
            type="UnloadModel",
            id=str(uuid.uuid4()),
            body=body.model_dump(),
        )
        fut = self.registry.expect_command_result(env.id, timeout=self.command_timeout)
        ok = await self.registry.send(node_id, env)
        if not ok:
            raise NodeUnreachableError(f"failed to send to {node_id}")
        return await fut


__all__ = ["WorkerController", "NodeUnreachableError"]

import uuid
import os
import logging
from app.provisioning.base import Provisioner

logger = logging.getLogger(__name__)


if os.getenv("INFERIA_ENV") != "container":
    raise RuntimeError("SkyPilot provisioner must run in container")


class SkyPilotProvisioner(Provisioner):

    async def provision(self, request) -> str:
        try:
            import sky
        except ImportError as e:
            raise RuntimeError(
                "SkyPilot is not available on this platform"
            ) from e

        cluster_name = f"inferia-{uuid.uuid4().hex[:8]}"

        logger.info(
            "Provisioning cluster via SkyPilot",
            extra={
                "cluster": cluster_name,
                "cloud": request.cloud,
                "region": request.region,
                "gpu": request.gpu,
                "gpu_type": request.gpu_type,
                "cpu": request.cpu,
            },
        )

        resources = sky.Resources(
            cloud=request.cloud,
            region=request.region,
            accelerators={request.gpu_type: request.gpu},
            cpus=request.cpu,
        )

        task = sky.Task(
            name="inferia-node-bootstrap",
            resources=resources,
            run="echo Inferia node bootstrap",
        )

        try:
            sky.launch(
                task,
                cluster_name=cluster_name,
                detach_run=True,
                idle_minutes_to_autostop=None,
            )
        except Exception as e:
            raise RuntimeError(f"SkyPilot provision failed: {e}")

        return cluster_name

    async def terminate(self, cluster_id: str):
        try:
            import sky
            logger.info("Terminating SkyPilot cluster", extra={"cluster": cluster_id})
            sky.down(cluster_name=cluster_id)
        except Exception:
            # best-effort termination
            pass

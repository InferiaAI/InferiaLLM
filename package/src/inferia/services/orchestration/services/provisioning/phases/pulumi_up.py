"""PulumiUpHandler — wraps run_pulumi_up_sync in asyncio.to_thread."""
from __future__ import annotations

import asyncio

from inferia.services.orchestration.services.adapter_engine.adapters.pulumi.programs import (
    build_program,
)
from inferia.services.orchestration.services.adapter_engine.adapters.pulumi.pulumi_aws_adapter import (
    run_pulumi_up_sync,
)
from inferia.services.orchestration.services.provisioning.jobs.model import (
    Phase, PhaseResult, ProvisioningJob,
)
from inferia.services.orchestration.services.provisioning.phases.base import (
    PhaseContext,
)


class PulumiUpHandler:
    """Phase: PROVISIONING. Runs pulumi up via asyncio.to_thread.

    The Pulumi Python SDK has no up_async (memory:
    feedback_pulumi_python_sdk_sync). All exceptions propagate; the
    classifier decides retry vs fail.
    """

    name = Phase.PROVISIONING

    async def run(self, job: ProvisioningJob, ctx: PhaseContext) -> PhaseResult:
        stack_name = f"{job.org_id}-{job.pool_id}-{job.node_id}"
        spec = job.spec
        program = build_program(spec=spec, stack_outputs=job.pulumi_stack_outputs or {})

        await ctx.emit_event(
            pool_id=job.pool_id, node_id=job.node_id, phase=Phase.PROVISIONING,
            status="running",
            message=f"Starting pulumi up on stack {stack_name}",
        )

        # Run in a thread — see feedback_pulumi_python_sdk_sync.
        outputs = await asyncio.to_thread(
            run_pulumi_up_sync,
            stack_name=stack_name,
            program=program,
            env=ctx.pulumi_env,
        )

        await ctx.emit_event(
            pool_id=job.pool_id, node_id=job.node_id, phase=Phase.PROVISIONING,
            status="succeeded",
            message=f"EC2 instance {outputs.instance_id} created in {outputs.region}",
            extra={"instance_id": outputs.instance_id,
                     "public_dns": outputs.public_dns},
        )

        return PhaseResult(
            next_phase=Phase.BOOTSTRAPPING,
            outputs={
                "instance_id": outputs.instance_id,
                "public_dns": outputs.public_dns,
                "region": outputs.region,
                "ami_id": outputs.ami_id,
            },
        )

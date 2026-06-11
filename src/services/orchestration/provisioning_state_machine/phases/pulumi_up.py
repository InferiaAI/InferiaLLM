"""PulumiUpHandler — wraps run_pulumi_up_sync in asyncio.to_thread."""
from __future__ import annotations

import asyncio

from providers.pulumi.programs import (
    build_program,
)
from providers.pulumi.pulumi_aws_adapter import (
    run_pulumi_up_sync,
)
from services.orchestration.provisioning_state_machine.jobs.model import (
    Phase, PhaseResult, ProvisioningJob,
)
from services.orchestration.provisioning_state_machine.phases.base import (
    PhaseContext, stack_name_for_job,
)


class PulumiUpHandler:
    """Phase: PROVISIONING. Runs pulumi up via asyncio.to_thread.

    The Pulumi Python SDK has no up_async (memory:
    feedback_pulumi_python_sdk_sync). All exceptions propagate; the
    classifier decides retry vs fail.
    """

    name = Phase.PROVISIONING

    async def run(self, job: ProvisioningJob, ctx: PhaseContext) -> PhaseResult:
        stack_name = stack_name_for_job(job)
        spec = dict(job.spec or {})
        provider = (spec.get("provider") or getattr(job, "provider", None) or "aws").lower()

        # AWS: mint a single-use bootstrap token + build the cloud-init
        # user_data right before launch (provision-time, not deploy-time).
        # The EC2 boots this script, runs the inferia-worker container, and
        # the worker self-registers onto the placeholder's compute_inventory
        # row so BootstrapHandler (which polls job.node_id) sees it go ready.
        if provider == "aws" and not spec.get("user_data"):
            spec = await self._inject_aws_bootstrap(job, ctx, spec)

        # Thread the placeholder's node_id into the spec so the launch
        # program stamps an InferiaNodeId tag on the instance. The boto3
        # orphan sweep (aws_orphan_sweep) keys off this tag to reclaim
        # leaked EC2 that Pulumi state never tracked.
        spec.setdefault("node_id", str(job.node_id))

        program = build_program(spec=spec, stack_outputs=job.pulumi_stack_outputs or {})

        await ctx.emit_event(
            pool_id=job.pool_id, node_id=job.node_id, phase=Phase.PROVISIONING,
            status="running",
            message=f"Starting pulumi up on stack {stack_name}",
        )

        # Pin Pulumi to a persistent LOCAL file backend. Without
        # PULUMI_BACKEND_URL the automation API defaults to Pulumi Cloud and
        # `pulumi up` fails / prompts for login. state_dir must persist so
        # deprovision_node can reopen the stack to destroy it later.
        import os as _os
        from services.orchestration.config import settings
        state_dir = settings.pulumi_state_dir
        try:
            _os.makedirs(state_dir, exist_ok=True)
        except OSError:
            # Best-effort — in production the container runs as root and the
            # dir is writable; if not, run_pulumi_up_sync surfaces a clearer
            # error. Don't crash the handler here (also lets tests that mock
            # run_pulumi_up_sync run without a writable state dir).
            pass
        env = dict(ctx.pulumi_env or {})
        env.setdefault("PULUMI_BACKEND_URL", f"file://{state_dir}")
        env.setdefault("PULUMI_CONFIG_PASSPHRASE", settings.pulumi_passphrase or "")
        # Override region to match the pool spec — otherwise Pulumi creates the
        # instance in the credentials' default region (e.g. eu-north-1) and the
        # terraform provider can't find the AMI there, surfacing as the cryptic
        # "collecting instance settings: couldn't find resource" error.
        if spec.get("region"):
            env["AWS_DEFAULT_REGION"] = spec["region"]

        # Run in a thread — see feedback_pulumi_python_sdk_sync.
        outputs = await asyncio.to_thread(
            run_pulumi_up_sync,
            stack_name=stack_name,
            program=program,
            env=env,
            state_dir=state_dir,
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
                "private_ip": getattr(outputs, "private_ip", None),
                # Only overwrite region/ami_id when the stack actually exported
                # them — otherwise keep the values PreflightHandler resolved.
                # (The '||' jsonb merge in transition_to would otherwise clobber
                # good preflight values with None.)
                **({"region": outputs.region} if outputs.region else {}),
                **({"ami_id": outputs.ami_id} if outputs.ami_id else {}),
            },
        )

    async def _inject_aws_bootstrap(
        self, job: ProvisioningJob, ctx: PhaseContext, spec: dict,
    ) -> dict:
        """Mint a bootstrap token + build cloud-init user_data for an AWS
        node, returning a spec augmented with ``user_data`` + ``bootstrap_id``.

        node_name is the placeholder's own node_name (``node-<node_id>``) so
        the worker's ON CONFLICT(pool_id, node_name) upsert updates the SAME
        compute_inventory row job.node_id points at — without this the worker
        creates a new row and BootstrapHandler's poll on the placeholder times
        out forever.
        """
        from services.orchestration.worker_controller.auth import (
            mint_bootstrap_token,
        )
        from providers.aws.bootstrap_builder import (
            build_user_data,
        )
        from services.orchestration.repositories.pool_repo import (
            ComputePoolRepository,
        )
        from services.orchestration.config import settings

        node_name = None
        async with ctx.db.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT node_name FROM compute_inventory WHERE id = $1",
                job.node_id,
            )
            if row and row["node_name"]:
                node_name = row["node_name"]
        if not node_name:
            node_name = f"node-{job.node_id}"

        pool_repo = ComputePoolRepository(ctx.db)
        inference_token = await pool_repo.get_or_generate_inference_token(
            pool_id=job.pool_id,
        )

        # Operator SSH authorized_keys (mounted into the CP at this path) so
        # the provisioned worker accepts SSH for ops/debugging. Best-effort:
        # absent file → SSH stays disabled (the prior behaviour).
        ssh_authorized_keys = ""
        try:
            import os as _os
            _ssh_path = (
                getattr(settings, "ssh_authorized_keys_path", None)
                or "/var/lib/inferia/ssh/authorized_keys"
            )
            if _os.path.exists(_ssh_path):
                with open(_ssh_path) as _f:
                    ssh_authorized_keys = _f.read()
        except OSError:
            ssh_authorized_keys = ""

        async with ctx.db.acquire() as conn:
            token, bootstrap_id = await mint_bootstrap_token(
                conn, pool_id=job.pool_id, org_id=job.org_id,
            )

        user_data = build_user_data(
            bootstrap_token=token,
            control_plane_url=settings.control_plane_external_url,
            node_name=node_name,
            pool_id=str(job.pool_id),
            image=settings.worker_image,
            image_tag=str(spec.get("worker_image_tag") or settings.worker_image_tag or ""),
            inference_token=inference_token,
            instance_class=str(spec.get("instance_class") or "normal_gpu"),
            gpu_count=int(spec.get("gpu_count") or 1),
            ssh_authorized_keys=ssh_authorized_keys,
        )
        return {**spec, "user_data": user_data, "bootstrap_id": str(bootstrap_id)}

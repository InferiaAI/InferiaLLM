"""Integration test: kill reconciler mid-pulumi-up, restart, resume.

Strategy:

1. Override ``lease_seconds`` to a tiny value (0.2s) so the lease expires
   quickly inside the test.
2. Patch ``run_pulumi_up_sync`` so its first invocation ``time.sleep``-s
   past the lease TTL, then raises -- simulating a stalled stack.up that
   crashes after losing its lease. The handler is wrapped in
   ``asyncio.to_thread`` so the sleep does NOT block the event loop; the
   renewer continues to run on the main loop and can attempt to extend
   the lease independently.
3. The reconciler escalates the raised RuntimeError through the
   classifier; it gets ``UNCLASSIFIED`` (TRANSIENT by default) and the
   row is scheduled for retry. We also manually clear the lease columns
   to simulate the reconciler crashing without a clean release -- the
   next claim_next_job picks the job up regardless.
4. The second pulumi_up call returns a successful StackOutputs; drive
   ticks until ready.

Why this proves crash recovery: Pulumi's ``stack.up`` is idempotent on
the same stack name, so retrying a half-completed stack picks up where
it left off. The provisioning_jobs row is the durable cursor -- a fresh
reconciler boot (or the same reconciler claiming the expired-lease job
again) resumes from phase=provisioning without re-running preflight.
"""
from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest


@pytest.mark.asyncio
async def test_lease_expiry_re_picks_up_job(app_with_real_db):
    from providers.pulumi.pulumi_aws_adapter import (
        StackOutputs,
    )
    app, client, pool = app_with_real_db

    pulumi_calls = {"n": 0}

    def _pulumi_first_hangs_then_succeeds(*, stack_name, program, env):
        pulumi_calls["n"] += 1
        if pulumi_calls["n"] == 1:
            # Simulate a stalled stack.up -- sleep so the 0.2s test lease
            # expires while we're "running", then crash. PulumiUpHandler
            # runs this in asyncio.to_thread, so the sleep does NOT block
            # the event loop -- the renewer keeps running.
            import time
            time.sleep(0.6)
            raise RuntimeError("simulated mid-pulumi crash")
        return StackOutputs(
            instance_id="i-abc",
            public_dns=None,
            region="us-east-1",
            ami_id="ami-abc",
        )

    with patch(
        "services.orchestration.provisioning_state_machine.phases."
        "preflight.verify_credentials", return_value={"Account": "123"},
    ), patch(
        "services.orchestration.provisioning_state_machine.phases."
        "preflight.resolve_ami", return_value="ami-abc",
    ), patch(
        "services.orchestration.provisioning_state_machine.phases."
        "preflight.verify_subnet_exists", return_value=None,
    ), patch(
        "services.orchestration.provisioning_state_machine.phases."
        "preflight.verify_security_group_exists", return_value=None,
    ), patch(
        "services.orchestration.provisioning_state_machine.phases."
        "pulumi_up.run_pulumi_up_sync",
        side_effect=_pulumi_first_hangs_then_succeeds,
    ):
        resp = await client.post(
            "/v1/nodes/add/aws",
            json={"spec": {"instance_class": "normal_gpu",
                           "instance_type":  "g6.xlarge",
                           "region":         "us-east-1"}},
            headers={"X-Organization-ID": "org-int",
                     "Authorization": "Bearer test"},
        )
        assert resp.status_code == 200, resp.text
        node_id = resp.json()["node_id"]

        # Drive the reconciler with a SHORT 0.2s lease so we can simulate
        # crash + recovery in test time. lease_seconds flows into Postgres
        # via make_interval(secs => $) which accepts fractional values.
        rec1 = app.state.reconciler
        rec1.lease_seconds = 0.2

        # Tick 1: preflight -> provisioning. Patched verify_credentials
        # + resolve_ami + subnet + sg short-circuit AWS, so preflight
        # succeeds and the next claim will pick up phase=provisioning.
        await rec1.tick_once()

        # Tick 2: provisioning. First call sleeps past lease TTL then
        # raises -- the reconciler's exception handling classifies the
        # RuntimeError, schedules retry, and returns. We swallow any
        # exception that bubbles up because we deliberately raised one.
        try:
            await rec1.tick_once()
        except Exception:
            pass

        # Manually expire the lease -- simulates a reconciler process
        # dying without releasing its lease. The next claim must still
        # pick this row up because lease_expires_at < now(). We also
        # clear next_attempt_after so the backoff schedule doesn't keep
        # the row out of claimable rotation for the rest of the test.
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE provisioning_jobs SET lease_holder=NULL, "
                "lease_expires_at=NULL, next_attempt_after=NULL "
                "WHERE node_id=$1",
                uuid.UUID(node_id),
            )

        # Drive subsequent ticks. Second pulumi_up call returns a
        # successful StackOutputs; the job advances provisioning ->
        # bootstrapping. When bootstrapping is reached, flip the
        # compute_inventory row to 'ready' so BootstrapHandler exits
        # its polling loop and the job lands in phase='ready'.
        for _ in range(6):
            await rec1.tick_once()
            async with pool.acquire() as conn:
                phase = await conn.fetchval(
                    "SELECT phase FROM provisioning_jobs WHERE node_id=$1",
                    uuid.UUID(node_id),
                )
                if phase == "bootstrapping":
                    await conn.execute(
                        "UPDATE compute_inventory SET state='ready' "
                        "WHERE id=$1",
                        uuid.UUID(node_id),
                    )

        body = (await client.get(
            f"/v1/nodes/{node_id}/provisioning",
            headers={"Authorization": "Bearer test"},
        )).json()
        assert body["current_phase"] == "ready", body
        assert body["terminal"] is True
        # First call crashed, second call succeeded -- proves the
        # state-machine row was the durable cursor across the crash.
        assert pulumi_calls["n"] == 2, (
            f"expected exactly 2 pulumi calls, got {pulumi_calls['n']}"
        )

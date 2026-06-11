"""Integration test: a PERMANENT failure -> POST /retry -> success.

Walks the full retry round-trip:

1. First run patches verify_credentials to raise InvalidCredentialsError
   (PERMANENT). After one tick the job lands in phase='failed' with
   error.code='INVALID_CREDENTIALS' exposed via GET /provisioning.
2. POST .../provisioning/retry resets the row to phase='preflight' and
   clears attempt_count + error_* columns (the reset_for_retry repo
   method does this atomically).
3. Second run patches everything to succeed. After enough ticks the
   job reaches phase='ready' with attempt_count back to 0 — proving
   the retry reset truly wiped the failure history.
"""
from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest


@pytest.mark.asyncio
async def test_failed_job_retried_to_ready(app_with_real_db):
    from providers.pulumi.pulumi_aws_adapter import (
        StackOutputs,
    )
    from services.orchestration.provisioning_state_machine.errors import (
        InvalidCredentialsError,
    )
    app, client, pool = app_with_real_db

    # --- Run 1: creds fail -> phase=failed --------------------------------
    with patch(
        "services.orchestration.provisioning_state_machine.phases."
        "preflight.verify_credentials",
        side_effect=InvalidCredentialsError("bad creds"),
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

        rec = app.state.reconciler
        await rec.tick_once()  # preflight fails

        body = (await client.get(
            f"/v1/nodes/{node_id}/provisioning",
            headers={"Authorization": "Bearer test"},
        )).json()
        assert body["current_phase"] == "failed"
        # error.code matches the classifier output for InvalidCredentialsError
        assert body["error"]["code"] == "INVALID_CREDENTIALS"
        assert body["terminal"] is True

    # --- POST /retry resets to preflight ---------------------------------
    resp = await client.post(
        f"/v1/nodes/{node_id}/provisioning/retry",
        headers={"Authorization": "Bearer test"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["phase"] == "preflight"

    # --- Run 2: creds work + pulumi works -> ready ------------------------
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
        return_value=StackOutputs(
            instance_id="i-abc", public_dns="ec2.x.compute.amazonaws.com",
            region="us-east-1", ami_id="ami-abc",
        ),
    ):
        for _ in range(6):
            await rec.tick_once()
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
        assert body["current_phase"] == "ready"
        # attempt_count was reset to 0 by reset_for_retry, and the
        # successful Run 2 path never bumps attempt_count, so we expect 0.
        assert body["attempt_count"] == 0
        assert body["error"] is None

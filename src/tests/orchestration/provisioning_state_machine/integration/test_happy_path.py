"""End-to-end happy-path integration test.

POST /v1/nodes/add/aws -> reconciler runs all phases -> ready.

Uses:
- real Postgres (gated on INFERIA_TEST_DATABASE_URL)
- patched verify_credentials / resolve_ami / subnet / sg checks
- patched run_pulumi_up_sync that returns a fixed StackOutputs

The fixture in conftest.py exposes app.state.reconciler so the test
drives the state machine deterministically via tick_once.
"""
from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest


@pytest.mark.asyncio
async def test_full_happy_path_to_ready(app_with_real_db):
    """POST add/aws -> drive reconciler ticks -> phase=ready, inventory=ready."""
    from providers.pulumi.pulumi_aws_adapter import (
        StackOutputs,
    )
    app, client, pool = app_with_real_db

    # Patch the AWS-touching functions. The patch targets are the names
    # in the phase modules (preflight, pulumi_up) — that's the import
    # path the handlers actually resolve at call time, so patching here
    # short-circuits boto3 / Pulumi without injecting mock creds.
    with patch(
        "orchestration.provisioning_state_machine.phases."
        "preflight.verify_credentials", return_value={"Account": "123"},
    ), patch(
        "orchestration.provisioning_state_machine.phases."
        "preflight.resolve_ami", return_value="ami-abc",
    ), patch(
        "orchestration.provisioning_state_machine.phases."
        "preflight.verify_subnet_exists", return_value=None,
    ), patch(
        "orchestration.provisioning_state_machine.phases."
        "preflight.verify_security_group_exists", return_value=None,
    ), patch(
        "orchestration.provisioning_state_machine.phases."
        "pulumi_up.run_pulumi_up_sync",
        return_value=StackOutputs(
            instance_id="i-abc",
            public_dns="ec2-x.compute.amazonaws.com",
            region="us-east-1",
            ami_id="ami-abc",
        ),
    ):
        # Submit the request via the thin-enqueue HTTP path. The
        # response returns immediately with node_id + job_id while the
        # reconciler picks up the work asynchronously.
        resp = await client.post(
            "/v1/nodes/add/aws",
            json={"spec": {
                "instance_class": "normal_gpu",
                "instance_type":  "g6.xlarge",
                "region":         "us-east-1",
            }},
            headers={"X-Organization-ID": "org-int",
                     "Authorization": "Bearer test"},
        )
        assert resp.status_code == 200, resp.text
        node_id = resp.json()["node_id"]

        # Drive reconciler ticks until ready. Each tick advances one
        # phase; the BOOTSTRAPPING phase needs us to simulate worker
        # registration by flipping compute_inventory.state to 'ready'
        # — without that the bootstrap handler polls forever and the
        # test would hang on its bootstrap_timeout.
        rec = app.state.reconciler
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

        # Assert terminal state via the HTTP surface — same shape the
        # dashboard's InstanceDetail consumes.
        resp = await client.get(
            f"/v1/nodes/{node_id}/provisioning",
            headers={"Authorization": "Bearer test"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["current_phase"] == "ready"
        assert body["terminal"] is True
        assert body["aws_metadata"]["instance_id"] == "i-abc"
        assert body["aws_metadata"]["region"] == "us-east-1"
        assert body["aws_metadata"]["ami_id"] == "ami-abc"
        assert body["error"] is None

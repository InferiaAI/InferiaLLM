"""Integration test: DELETE during PROVISIONING -> CancelHandler runs destroy.

End-to-end:

1. POST /v1/nodes/add/aws + drive one tick -> job lands in
   phase='provisioning' (preflight passed in the same tick).
2. DELETE /v1/nodes/{id} -> request_cancel flips the job's phase to
   'cancelling' (the route returns 204).
3. The next reconciler tick claims the cancelling job; CancelHandler
   runs (patched) run_pulumi_destroy_sync and transitions to terminated.
4. Assert destroy was called AND the canonical leak-proof teardown ran:
   BOTH the provisioning_jobs row and the compute_inventory row are GONE.
   Task 2.3 replaced the old soft state='terminated' write with
   purge_node, which hard-deletes the inventory row + provisioning_jobs +
   events + tokens in one tx (the soft write leaked those rows forever).
"""
from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest


@pytest.mark.asyncio
async def test_delete_mid_provision_triggers_cancel(app_with_real_db):
    from providers.pulumi.pulumi_aws_adapter import (
        StackOutputs,
    )
    app, client, pool = app_with_real_db

    destroy_called = {"value": False}

    def _fake_destroy(*, stack_name, program, env, state_dir=None):
        destroy_called["value"] = True

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
            instance_id="i-abc", public_dns=None,
            region="us-east-1", ami_id="ami-abc",
        ),
    ), patch(
        "orchestration.provisioning_state_machine.phases."
        "cancel.run_pulumi_destroy_sync", side_effect=_fake_destroy,
    ), patch(
        # The reconciler's teardown runs a boto3 tag-based orphan sweep on
        # the TERMINATED transition. Patch it so the test never touches AWS;
        # its own unit tests cover the sweep internals.
        "orchestration.adapter_engine."
        "aws_orphan_sweep.sweep_node_instances", return_value=[],
    ) as sweep:
        # 1. Submit + drive one tick (preflight -> provisioning).
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
        await rec.tick_once()  # preflight -> provisioning

        # 2. DELETE before bootstrapping completes. The route returns
        # 204 (request_cancel succeeded) for non-terminal jobs; the
        # legacy AWS-destroy path is bypassed when a provisioning_jobs
        # row owns the lifecycle (see api/nodes.py::delete_node).
        resp = await client.delete(
            f"/v1/nodes/{node_id}",
            headers={"Authorization": "Bearer test"},
        )
        assert resp.status_code in (200, 204), resp.text

        # 3. Drive the cancel tick. The claim query orders 'cancelling'
        # jobs ahead of fresh work so this picks up our job immediately.
        await rec.tick_once()

        # 4. Assert destroy ran AND the canonical leak-proof teardown
        # purged ALL of the node's DB residue: the provisioning_jobs row
        # AND the compute_inventory row are both GONE (Task 2.3 replaced the
        # old soft state='terminated' write with a hard purge_node).
        assert destroy_called["value"], (
            "run_pulumi_destroy_sync was not invoked by CancelHandler"
        )
        # The orphan-EC2 sweep ran with (node_id, region) as a backstop.
        sweep.assert_called_once_with(node_id, "us-east-1")
        async with pool.acquire() as conn:
            job_count = await conn.fetchval(
                "SELECT count(*) FROM provisioning_jobs WHERE node_id=$1",
                uuid.UUID(node_id),
            )
            node_count = await conn.fetchval(
                "SELECT count(*) FROM compute_inventory WHERE id=$1",
                uuid.UUID(node_id),
            )
            event_count = await conn.fetchval(
                "SELECT count(*) FROM node_provisioning_events WHERE node_id=$1",
                uuid.UUID(node_id),
            )
        assert job_count == 0, "provisioning_jobs row must be purged"
        assert node_count == 0, "compute_inventory row must be purged"
        assert event_count == 0, "node_provisioning_events must be purged"

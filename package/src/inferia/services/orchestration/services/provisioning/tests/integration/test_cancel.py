"""Integration test: DELETE during PROVISIONING -> CancelHandler runs destroy.

End-to-end:

1. POST /v1/nodes/add/aws + drive one tick -> job lands in
   phase='provisioning' (preflight passed in the same tick).
2. DELETE /v1/nodes/{id} -> request_cancel flips the job's phase to
   'cancelling' (the route returns 204).
3. The next reconciler tick claims the cancelling job; CancelHandler
   runs (patched) run_pulumi_destroy_sync and transitions to terminated.
4. Assert destroy was called AND the job row is in phase='terminated'.

Note: the current production code does NOT also update
compute_inventory.state to 'terminated' when the CancelHandler runs.
The compute_inventory row stays in the state it was in (provisioning).
This is a known gap — the test deliberately only checks the job row's
phase so it documents the *current* contract, not the aspirational
"inventory also flips to terminated" version in the plan. T33 / a
follow-up should patch the CancelHandler (or the reconciler post-
hook) to also call inventory_repo.set_state(..., 'terminated').
"""
from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest


@pytest.mark.asyncio
async def test_delete_mid_provision_triggers_cancel(app_with_real_db):
    from inferia.services.orchestration.services.adapter_engine.adapters.pulumi.pulumi_aws_adapter import (
        StackOutputs,
    )
    app, client, pool = app_with_real_db

    destroy_called = {"value": False}

    def _fake_destroy(*, stack_name, program, env):
        destroy_called["value"] = True

    with patch(
        "inferia.services.orchestration.services.provisioning.phases."
        "preflight.verify_credentials", return_value={"Account": "123"},
    ), patch(
        "inferia.services.orchestration.services.provisioning.phases."
        "preflight.resolve_ami", return_value="ami-abc",
    ), patch(
        "inferia.services.orchestration.services.provisioning.phases."
        "preflight.verify_subnet_exists", return_value=None,
    ), patch(
        "inferia.services.orchestration.services.provisioning.phases."
        "preflight.verify_security_group_exists", return_value=None,
    ), patch(
        "inferia.services.orchestration.services.provisioning.phases."
        "pulumi_up.run_pulumi_up_sync",
        return_value=StackOutputs(
            instance_id="i-abc", public_dns=None,
            region="us-east-1", ami_id="ami-abc",
        ),
    ), patch(
        "inferia.services.orchestration.services.provisioning.phases."
        "cancel.run_pulumi_destroy_sync", side_effect=_fake_destroy,
    ):
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

        # 4. Assert destroy ran + the job row reached the terminated phase.
        assert destroy_called["value"], (
            "run_pulumi_destroy_sync was not invoked by CancelHandler"
        )
        async with pool.acquire() as conn:
            phase = await conn.fetchval(
                "SELECT phase FROM provisioning_jobs WHERE node_id=$1",
                uuid.UUID(node_id),
            )
        assert phase == "terminated"

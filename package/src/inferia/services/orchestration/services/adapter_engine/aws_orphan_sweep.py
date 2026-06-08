"""Tag-based orphan / duplicate EC2 sweep — a boto3 backstop for the
Pulumi ``destroy`` path.

``pulumi destroy`` only frees instances recorded in the stack state. A
retry that double-launches an EC2 (or any instance Pulumi lost track of)
leaks forever — observed live as 2x g6.xlarge running under a single
"terminated" pool (memory: project_orphaned_ec2_duplicate_launch_leak).

This module reclaims those leaks by *tag* rather than by stack state:

* :func:`sweep_node_instances` terminates every live instance carrying the
  per-NODE tag ``InferiaNodeId`` (written by the EC2 launch program in
  ``adapters/pulumi/programs.py``).
* :func:`sweep_pool_instances` does the same for the per-POOL tag
  ``InferiaPoolId`` (catches every node of a pool in one pass).

Design notes
------------
* **Best-effort.** Every AWS call is wrapped so a transient error is
  logged and the function returns ``[]`` rather than raising — this is a
  backstop that runs *after* the authoritative Pulumi destroy, so a flaky
  ``describe`` must never break the caller's teardown flow. A successful
  sweep still emits a clear log line listing the terminated ids.

* **Injectable client seam.** The boto3 client is built lazily in
  :func:`_ec2_client` and credentials are resolved in
  :func:`_resolve_aws_creds`, so neither boto3 nor the DB-backed
  ProvidersConfig is imported at module load. Tests monkeypatch these two
  names to inject a fake client without touching real AWS — mirroring the
  ``_boto3_sts_client`` seam in ``adapters/pulumi/credentials.py`` and the
  injectable-factory style of ``aws_deprovision.py`` /
  ``test_aws_deprovision.py``.

* **Sync.** boto3 is synchronous; async callers (e.g. the reconciler's
  CancelHandler) wrap these in ``asyncio.to_thread(...)``.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# EC2 lifecycle states worth terminating. We deliberately exclude
# 'shutting-down' and 'terminated' (already gone / going) and 'rebooting'
# (transient). Anything billable or resumable is fair game for the sweep.
_LIVE_STATES = ["pending", "running", "stopping", "stopped"]


def _resolve_aws_creds(credential_name: str | None = None) -> dict[str, str]:
    """Return boto3 client credential kwargs, or ``{}`` to fall back to the
    default boto3 credential chain.

    The orchestration container receives AWS creds the same way the Pulumi
    adapter consumes them — via ``AWS_ACCESS_KEY_ID`` /
    ``AWS_SECRET_ACCESS_KEY`` env vars (see ``resolve_aws_env`` in
    ``adapters/pulumi/credentials.py``, which exports exactly these). We
    read them lazily here so importing this module never requires boto3 or
    a DB session, and so tests can monkeypatch this function wholesale.

    ``credential_name`` is accepted for signature symmetry with the rest of
    the adapter layer (``provider_credential_name``); the current
    single-credential model resolves the same env-backed creds regardless,
    matching ``PulumiAWSAdapter.deprovision_node`` which also ignores it.

    Returning ``{}`` lets :func:`_ec2_client` fall back to the boto3
    default chain (instance profile / shared config) — the same graceful
    fallback ``latest_dlami_ami``/``resolve_ami`` use.
    """
    import os

    key = os.environ.get("AWS_ACCESS_KEY_ID")
    secret = os.environ.get("AWS_SECRET_ACCESS_KEY")
    if not (key and secret):
        return {}
    creds: dict[str, str] = {
        "aws_access_key_id": key,
        "aws_secret_access_key": secret,
    }
    token = os.environ.get("AWS_SESSION_TOKEN")
    if token:
        creds["aws_session_token"] = token
    return creds


def _ec2_client(region: str, *, credential_name: str | None = None):
    """Build a boto3 EC2 client for ``region``.

    Separate function (like ``_boto3_sts_client`` in the pulumi credentials
    module) so tests can monkeypatch it and never import boto3 / hit AWS.
    """
    import boto3

    return boto3.client(
        "ec2",
        region_name=region,
        **_resolve_aws_creds(credential_name),
    )


def _instance_ids_from_describe(resp: dict[str, Any]) -> list[str]:
    """Flatten a describe_instances response into a list of instance ids."""
    ids: list[str] = []
    for reservation in resp.get("Reservations", []) or []:
        for inst in reservation.get("Instances", []) or []:
            iid = inst.get("InstanceId")
            if iid:
                ids.append(iid)
    return ids


def _sweep_by_tag(
    *,
    tag_key: str,
    tag_value: str,
    region: str,
    credential_name: str | None,
) -> list[str]:
    """Describe + terminate every live instance carrying ``tag_key=tag_value``.

    Best-effort: any AWS error is logged and yields ``[]`` rather than
    raising. Returns the terminated instance ids (``[]`` when none match).
    """
    if not tag_value or not region:
        logger.warning(
            "aws_orphan_sweep skipped: tag_value=%r region=%r",
            tag_value, region,
        )
        return []

    filters = [
        {"Name": f"tag:{tag_key}", "Values": [tag_value]},
        {"Name": "instance-state-name", "Values": list(_LIVE_STATES)},
    ]
    try:
        client = _ec2_client(region, credential_name=credential_name)
        resp = client.describe_instances(Filters=filters)
        ids = _instance_ids_from_describe(resp)
        if not ids:
            logger.info(
                "aws_orphan_sweep: no live instances for %s=%s in %s",
                tag_key, tag_value, region,
            )
            return []
        client.terminate_instances(InstanceIds=ids)
    except Exception as e:  # noqa: BLE001 — best-effort backstop
        logger.warning(
            "aws_orphan_sweep best-effort failure for %s=%s in %s: %s: %s",
            tag_key, tag_value, region, type(e).__name__, e,
        )
        return []

    logger.info(
        "aws_orphan_sweep terminated %d instance(s) for %s=%s in %s: %s",
        len(ids), tag_key, tag_value, region, ", ".join(ids),
    )
    return ids


def sweep_node_instances(
    node_id: str,
    region: str,
    *,
    credential_name: str | None = None,
) -> list[str]:
    """Terminate every live EC2 tagged ``InferiaNodeId=<node_id>`` in
    ``region``. Backstop for the per-node Pulumi destroy. Returns the
    terminated instance ids (empty list if none / on best-effort failure)."""
    return _sweep_by_tag(
        tag_key="InferiaNodeId",
        tag_value=str(node_id or ""),
        region=region,
        credential_name=credential_name,
    )


def sweep_pool_instances(
    pool_id: str,
    region: str,
    *,
    credential_name: str | None = None,
) -> list[str]:
    """Terminate every live EC2 tagged ``InferiaPoolId=<pool_id>`` in
    ``region``. Backstop for the pool-wide Pulumi destroy (catches duplicate
    double-launches across all of a pool's nodes). Returns the terminated
    instance ids (empty list if none / on best-effort failure)."""
    return _sweep_by_tag(
        tag_key="InferiaPoolId",
        tag_value=str(pool_id or ""),
        region=region,
        credential_name=credential_name,
    )


__all__ = [
    "sweep_node_instances",
    "sweep_pool_instances",
]

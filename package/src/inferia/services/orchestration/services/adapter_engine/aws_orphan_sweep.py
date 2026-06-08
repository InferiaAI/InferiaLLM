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

* **Config-resolved creds (NOT the ambient env chain).** The control-plane
  container has NO ambient AWS creds, so a boto3 default-chain client raises
  ``NoCredentialsError`` and the whole backstop silently no-ops. Instead we
  resolve creds the *same* way the Pulumi ``destroy`` path does:
  ``resolve_aws_env(load_providers_config())`` (see
  ``PulumiAWSAdapter.discover_resources`` / ``get_logs`` /
  ``run_pulumi_destroy_sync`` in ``adapters/pulumi/pulumi_aws_adapter.py``,
  which all call ``resolve_aws_env(cfg)`` against the DB-backed
  ``ProvidersConfig``). ``load_providers_config`` is async (it opens an
  AsyncSession), but the sweep runs synchronously inside
  ``asyncio.to_thread(...)`` — a worker thread with no running loop — so we
  drive it to completion with ``asyncio.run(...)``. When no creds are
  configured (``MissingCredentialsError``) :func:`_resolve_aws_creds`
  returns ``{}`` and the sweep logs a clear "no creds" WARNING and returns
  ``[]`` (distinct from the "no instances" INFO line) — preserving the
  best-effort contract without ever falling back to the empty ambient
  chain.

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
    """Return boto3 client credential kwargs resolved from the DB-backed
    ``ProvidersConfig`` — the SAME source the Pulumi ``destroy`` path uses —
    or ``{}`` when no AWS creds are configured.

    The control-plane container has NO ambient AWS creds, so we must NOT
    rely on boto3's default credential chain (it raises
    ``NoCredentialsError`` and the sweep silently no-ops). Instead we mirror
    ``PulumiAWSAdapter.discover_resources`` / ``get_logs`` /
    ``run_pulumi_destroy_sync``, which all do
    ``env = resolve_aws_env(await load_providers_config())`` and pass
    ``env["AWS_ACCESS_KEY_ID"]`` / ``env["AWS_SECRET_ACCESS_KEY"]``
    explicitly to ``boto3.client``.

    ``load_providers_config`` is async (it opens an AsyncSession against the
    gateway DB), but this function is called synchronously from
    :func:`_sweep_by_tag`, which itself runs inside ``asyncio.to_thread`` —
    a worker thread with no running event loop — so ``asyncio.run`` drives
    the coroutine to completion safely. Imports are kept lazy so importing
    this module never requires boto3 or a DB session, and so tests can
    monkeypatch this function wholesale.

    ``credential_name`` is accepted for signature symmetry with the rest of
    the adapter layer (``provider_credential_name``); the current
    single-credential model resolves the config-level creds regardless,
    matching ``PulumiAWSAdapter.deprovision_node`` (which uses the
    config-level ``resolve_aws_env(cfg)``, not a per-name DB lookup).

    Returns ``{}`` when no creds are configured (``MissingCredentialsError``)
    so the caller can log a clear "no creds" WARNING and return ``[]`` —
    we deliberately do NOT fall back to the (empty) ambient chain.
    """
    import asyncio

    from inferia.services.orchestration.services.adapter_engine.adapters.pulumi.credentials import (
        MissingCredentialsError,
        resolve_aws_env,
    )
    from inferia.services.orchestration.services.adapter_engine.adapters.pulumi.pulumi_aws_adapter import (
        load_providers_config,
    )

    cfg = asyncio.run(load_providers_config())
    try:
        env = resolve_aws_env(cfg)
    except MissingCredentialsError:
        return {}

    creds: dict[str, str] = {
        "aws_access_key_id": env["AWS_ACCESS_KEY_ID"],
        "aws_secret_access_key": env["AWS_SECRET_ACCESS_KEY"],
    }
    # resolve_aws_env only exports AWS_SESSION_TOKEN when the config carried
    # one; forward it when present so STS temporary creds keep working.
    token = env.get("AWS_SESSION_TOKEN")
    if token:
        creds["aws_session_token"] = token
    return creds


def _ec2_client(region: str, *, creds: dict[str, str]):
    """Build a boto3 EC2 client for ``region`` with explicit ``creds``.

    Separate function (like ``_boto3_sts_client`` in the pulumi credentials
    module) so tests can monkeypatch it and never import boto3 / hit AWS.
    Credentials are resolved by the caller (:func:`_sweep_by_tag`) via
    :func:`_resolve_aws_creds` and passed in explicitly — mirroring how
    ``PulumiAWSAdapter.discover_resources`` builds its EC2 client.
    """
    import boto3

    return boto3.client(
        "ec2",
        region_name=region,
        **creds,
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

    # Resolve creds the same way the Pulumi destroy path does. No creds ⇒
    # the backstop can't authenticate; log a clear (no-creds-specific)
    # WARNING and bail, distinct from the "no instances" INFO line below.
    # Best-effort: a failure resolving creds (DB down, etc.) is logged and
    # also yields [] rather than raising.
    try:
        creds = _resolve_aws_creds(credential_name)
    except Exception as e:  # noqa: BLE001 — best-effort backstop
        logger.warning(
            "aws_orphan_sweep skipped for %s=%s in %s: failed to resolve AWS "
            "credentials: %s: %s",
            tag_key, tag_value, region, type(e).__name__, e,
        )
        return []
    if not creds:
        logger.warning(
            "aws_orphan_sweep skipped for %s=%s in %s: no AWS credentials "
            "configured (ProvidersConfig has no access_key_id/secret_access_key); "
            "orphan-EC2 backstop cannot authenticate",
            tag_key, tag_value, region,
        )
        return []

    filters = [
        {"Name": f"tag:{tag_key}", "Values": [tag_value]},
        {"Name": "instance-state-name", "Values": list(_LIVE_STATES)},
    ]
    try:
        client = _ec2_client(region, creds=creds)
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

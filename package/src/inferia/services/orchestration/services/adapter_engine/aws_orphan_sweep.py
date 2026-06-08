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
  :func:`_ec2_client`, so boto3 is never imported at module load. Tests
  monkeypatch this name to inject a fake client without touching real AWS —
  mirroring the ``_boto3_sts_client`` seam in
  ``adapters/pulumi/credentials.py`` and the injectable-factory style of
  ``aws_deprovision.py`` / ``test_aws_deprovision.py``.

* **Creds are resolved by the ASYNC caller and passed IN (not the ambient
  env chain, and NOT resolved here).** The control-plane container has NO
  ambient AWS creds, so a boto3 default-chain client raises
  ``NoCredentialsError`` and the whole backstop silently no-ops. Creds must
  instead be resolved the *same* way the Pulumi ``destroy`` path does —
  ``resolve_aws_env(load_providers_config())`` (see
  ``PulumiAWSAdapter.discover_resources`` / ``get_logs`` /
  ``run_pulumi_destroy_sync`` in ``adapters/pulumi/pulumi_aws_adapter.py``).

  ``load_providers_config`` is async (it opens an AsyncSession whose
  asyncpg connection is bound to the reconciler's MAIN event loop). These
  sweep functions run synchronously inside ``asyncio.to_thread(...)`` — a
  worker thread with NO running loop. Driving ``load_providers_config`` to
  completion *here* with ``asyncio.run(...)`` spins up a brand-new loop in
  that worker thread and blows up with
  ``RuntimeError: ... attached to a different loop`` (the asyncpg pool
  belongs to the main loop). So the sweep never actually authenticated in
  production — it logged "failed to resolve AWS credentials" and no-op'd.

  The fix: the ASYNC caller resolves creds on the main loop (where asyncpg
  works) and passes the resulting ``aws_env`` dict in. ``aws_env`` carries
  the keys ``resolve_aws_env`` exports — ``AWS_ACCESS_KEY_ID``,
  ``AWS_SECRET_ACCESS_KEY``, optionally ``AWS_SESSION_TOKEN`` — which
  :func:`_creds_from_aws_env` maps to boto3 ``client`` kwargs. When the
  caller could not resolve creds (no AWS config, DB down, …) it passes
  ``aws_env=None``; the sweep logs a clear "no creds" WARNING and returns
  ``[]`` (distinct from the "no instances" INFO line) — preserving the
  best-effort contract without ever falling back to the empty ambient chain.

* **Sync.** boto3 is synchronous; async callers (e.g. the reconciler's
  ``_teardown_node`` / pool finalizer / reaper) wrap these in
  ``asyncio.to_thread(...)`` and resolve ``aws_env`` first.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# EC2 lifecycle states worth terminating. We deliberately exclude
# 'shutting-down' and 'terminated' (already gone / going) and 'rebooting'
# (transient). Anything billable or resumable is fair game for the sweep.
_LIVE_STATES = ["pending", "running", "stopping", "stopped"]

_BUILDER_TAG = "inferia:engine-ami-builder"


async def resolve_sweep_aws_env() -> dict[str, str] | None:
    """Resolve the AWS creds env for the sweep — on the CURRENT (main) event
    loop, the SAME way the Pulumi ``destroy`` path does.

    MUST be awaited by the async caller (the reconciler ``_teardown_node`` /
    pool finalizer / reaper) BEFORE it hands the result to
    ``asyncio.to_thread(sweep_*_instances, ..., aws_env)``. ``load_providers_config``
    opens an AsyncSession whose asyncpg connection is bound to this loop;
    resolving here (not inside the ``to_thread`` worker) is what fixes the
    cross-loop ``RuntimeError`` that previously made the sweep a runtime no-op.

    Best-effort: a missing AWS config (``MissingCredentialsError``) or any
    resolution failure (DB down, etc.) is logged and yields ``None`` so the
    caller can still run the sweep (which then logs a no-creds WARNING and
    returns ``[]``) without breaking teardown. Imports are lazy so this module
    pulls in neither a DB session nor boto3 at import time.
    """
    from inferia.services.orchestration.services.adapter_engine.adapters.pulumi.credentials import (
        MissingCredentialsError,
        resolve_aws_env,
    )
    from inferia.services.orchestration.services.adapter_engine.adapters.pulumi.pulumi_aws_adapter import (
        load_providers_config,
    )

    try:
        cfg = await load_providers_config()
        return resolve_aws_env(cfg)
    except MissingCredentialsError:
        logger.warning(
            "aws_orphan_sweep: no AWS credentials configured "
            "(ProvidersConfig has no access_key_id/secret_access_key); "
            "the orphan-EC2 backstop will be skipped",
        )
        return None
    except Exception as e:  # noqa: BLE001 — best-effort backstop
        logger.warning(
            "aws_orphan_sweep: failed to resolve AWS credentials (%s: %s); "
            "the orphan-EC2 backstop will be skipped",
            type(e).__name__, e,
        )
        return None


def _creds_from_aws_env(aws_env: dict[str, str]) -> dict[str, str]:
    """Map an ``aws_env`` dict (as returned by ``resolve_aws_env(cfg)``) into
    boto3 ``client`` credential kwargs.

    ``resolve_aws_env`` exports ``AWS_ACCESS_KEY_ID`` /
    ``AWS_SECRET_ACCESS_KEY`` (and ``AWS_SESSION_TOKEN`` only when the config
    carried STS temporary creds). We forward each present value explicitly so
    the boto3 client authenticates with the SAME creds the Pulumi destroy path
    uses — never the (empty in the CP container) ambient default chain.
    """
    creds: dict[str, str] = {
        "aws_access_key_id": aws_env["AWS_ACCESS_KEY_ID"],
        "aws_secret_access_key": aws_env["AWS_SECRET_ACCESS_KEY"],
    }
    token = aws_env.get("AWS_SESSION_TOKEN")
    if token:
        creds["aws_session_token"] = token
    return creds


def _ec2_client(region: str, *, creds: dict[str, str]):
    """Build a boto3 EC2 client for ``region`` with explicit ``creds``.

    Separate function (like ``_boto3_sts_client`` in the pulumi credentials
    module) so tests can monkeypatch it and never import boto3 / hit AWS.
    Credentials are resolved by the async caller (via ``resolve_aws_env``),
    passed into the sweep as ``aws_env``, mapped by :func:`_creds_from_aws_env`
    and handed in explicitly — mirroring how
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
    aws_env: dict[str, str] | None,
) -> list[str]:
    """Describe + terminate every live instance carrying ``tag_key=tag_value``.

    ``aws_env`` is the creds dict the ASYNC caller resolved (on the main loop)
    via ``resolve_aws_env(load_providers_config())`` and passed in. When it is
    ``None`` / empty the backstop cannot authenticate; log a clear
    (no-creds-specific) WARNING and bail — distinct from the "no instances"
    INFO line, and never falling back to the empty ambient chain.

    Best-effort: any AWS error is logged and yields ``[]`` rather than
    raising. Returns the terminated instance ids (``[]`` when none match).
    """
    if not tag_value or not region:
        logger.warning(
            "aws_orphan_sweep skipped: tag_value=%r region=%r",
            tag_value, region,
        )
        return []

    # No creds ⇒ the backstop can't authenticate; the caller failed to
    # resolve them (no AWS config, DB down, etc.). Log a clear (no-creds-
    # specific) WARNING and bail, distinct from the "no instances" INFO line
    # below. We deliberately do NOT fall back to the empty ambient chain.
    if not aws_env:
        logger.warning(
            "aws_orphan_sweep skipped for %s=%s in %s: no AWS credentials "
            "resolved by the caller (ProvidersConfig missing creds or "
            "resolution failed); orphan-EC2 backstop cannot authenticate",
            tag_key, tag_value, region,
        )
        return []

    try:
        creds = _creds_from_aws_env(aws_env)
    except Exception as e:  # noqa: BLE001 — best-effort backstop
        # Malformed aws_env (missing AWS_ACCESS_KEY_ID, etc.) — treat like a
        # creds failure rather than crashing the caller's teardown flow.
        logger.warning(
            "aws_orphan_sweep skipped for %s=%s in %s: malformed AWS creds: "
            "%s: %s",
            tag_key, tag_value, region, type(e).__name__, e,
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
    aws_env: dict[str, str] | None = None,
) -> list[str]:
    """Terminate every live EC2 tagged ``InferiaNodeId=<node_id>`` in
    ``region``. Backstop for the per-node Pulumi destroy.

    ``aws_env`` is the creds dict the async caller resolved (on its main loop)
    via ``resolve_aws_env(load_providers_config())``; ``None`` ⇒ the sweep
    logs a no-creds WARNING and returns ``[]``. Returns the terminated
    instance ids (empty list if none / no creds / on best-effort failure)."""
    return _sweep_by_tag(
        tag_key="InferiaNodeId",
        tag_value=str(node_id or ""),
        region=region,
        aws_env=aws_env,
    )


def sweep_pool_instances(
    pool_id: str,
    region: str,
    aws_env: dict[str, str] | None = None,
) -> list[str]:
    """Terminate every live EC2 tagged ``InferiaPoolId=<pool_id>`` in
    ``region``. Backstop for the pool-wide Pulumi destroy (catches duplicate
    double-launches across all of a pool's nodes).

    ``aws_env`` is the creds dict the async caller resolved (on its main loop)
    via ``resolve_aws_env(load_providers_config())``; ``None`` ⇒ the sweep
    logs a no-creds WARNING and returns ``[]``. Returns the terminated
    instance ids (empty list if none / no creds / on best-effort failure)."""
    return _sweep_by_tag(
        tag_key="InferiaPoolId",
        tag_value=str(pool_id or ""),
        region=region,
        aws_env=aws_env,
    )


def sweep_stale_builders(
    region: str,
    aws_env: dict[str, str] | None = None,
    *,
    older_than_min: int = 30,
    now: "datetime | None" = None,
) -> list[str]:
    """Terminate engine-AMI builder instances (tag ``inferia:engine-ami-builder``)
    older than ``older_than_min`` minutes — reclaims a builder leaked by a CP
    crash mid-bake (the bake normally terminates its builder in a ``finally``).

    Best-effort like the rest of this module: no creds / AWS error → ``[]``,
    never raises. ``now`` is injectable for tests."""
    from datetime import datetime, timedelta, timezone

    if not aws_env:
        logger.warning("sweep_stale_builders skipped in %s: no AWS credentials", region)
        return []
    ref = now or datetime.now(timezone.utc)
    cutoff = ref - timedelta(minutes=older_than_min)
    try:
        creds = _creds_from_aws_env(aws_env)
        client = _ec2_client(region, creds=creds)
        resp = client.describe_instances(Filters=[
            {"Name": f"tag:{_BUILDER_TAG}", "Values": ["true"]},
            {"Name": "instance-state-name", "Values": list(_LIVE_STATES)},
        ])
        stale: list[str] = []
        for reservation in resp.get("Reservations", []) or []:
            for inst in reservation.get("Instances", []) or []:
                launched = inst.get("LaunchTime")
                iid = inst.get("InstanceId")
                if iid and launched and launched <= cutoff:
                    stale.append(iid)
    except Exception as e:  # noqa: BLE001 — best-effort backstop
        logger.warning(
            "sweep_stale_builders best-effort failure in %s: %s: %s",
            region, type(e).__name__, e,
        )
        return []

    if not stale:
        logger.info("sweep_stale_builders: no stale builders in %s", region)
        return []
    try:
        client.terminate_instances(InstanceIds=stale)
    except Exception as e:  # noqa: BLE001
        logger.warning("sweep_stale_builders terminate failed in %s: %s", region, e)
        return []
    logger.info(
        "sweep_stale_builders terminated %d in %s: %s",
        len(stale), region, ", ".join(stale),
    )
    return stale


__all__ = [
    "sweep_node_instances",
    "sweep_pool_instances",
    "sweep_stale_builders",
]

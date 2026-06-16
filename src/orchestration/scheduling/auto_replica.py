"""Auto-replica monitor: scale out a pool when a deployment's tokens_per_second
drops below the configured threshold.

Architecture
------------
A periodic background task (every 60s) queries RUNNING deployments where
``auto_replica_enabled = true``.  For each deployment it computes the average
``tokens_per_second`` over the last 5 minutes of inference logs.  If that
average is **below** the deployment's ``tokens_per_second_threshold`` AND
there is recent traffic (>= 3 requests in the window), it triggers a
scale-out by enqueueing a new provisioning job for the pool.

Scale-out guardrails
--------------------
- Cooldown: never scale the same deployment more than once per 10 minutes.
- Pool capacity: respect ``compute_pools.max_nodes``.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

logger = logging.getLogger("auto_replica")

COOLDOWN_MINUTES = 10
WINDOW_MINUTES = 5
MIN_REQUESTS = 3


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def _recent_avg_tps(db_pool, deployment_id: UUID) -> tuple[float | None, int]:
    """Average tokens_per_second over the last WINDOW_MINUTES for a deployment.

    Returns (avg_tps, request_count).  avg_tps is None when there are no
    non-null tps values in the window.
    """
    since = _utcnow() - timedelta(minutes=WINDOW_MINUTES)
    async with db_pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT tokens_per_second
              FROM inference_logs
             WHERE deployment_id = $1
               AND created_at >= $2
               AND tokens_per_second IS NOT NULL
               AND tokens_per_second > 0
             ORDER BY created_at DESC
            """,
            deployment_id,
            since,
        )
    if not rows:
        return None, 0
    vals = [r["tokens_per_second"] for r in rows]
    total = sum(vals)
    count = len(vals)
    return total / count, count


async def _pool_ready_nodes(db_pool, pool_id: UUID) -> int:
    """Count nodes in this pool whose state is 'ready' (excluding terminating)."""
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT COUNT(*) AS cnt
              FROM compute_inventory
             WHERE pool_id = $1
               AND state = 'ready'
               AND (metadata->>'terminating') IS DISTINCT FROM 'true'
            """,
            pool_id,
        )
    return row["cnt"] if row else 0


async def _pool_max_nodes(db_pool, pool_id: UUID) -> int | None:
    """Return the max_nodes cap for a pool, or None if unlimited."""
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT max_nodes FROM compute_pools WHERE id = $1",
            pool_id,
        )
    if row is None:
        return None
    return row["max_nodes"]


async def _build_pool_spec(db_pool, pool_id: UUID) -> dict | None:
    """Build a provisioning spec from pool metadata and an existing node's spec.

    Fetches the pool row, reads its metadata, and copies the instance_type /
    region / subnet / SG / AMI / root_volume / worker_image_tag from pool
    metadata or from a recent ready node in the same pool.
    """
    async with db_pool.acquire() as conn:
        pool_row = await conn.fetchrow(
            """
            SELECT id, provider, allowed_gpu_types, region_constraint,
                   metadata, owner_id, org_id
              FROM compute_pools
             WHERE id = $1
            """,
            pool_id,
        )
    if pool_row is None:
        return None

    # Normalise metadata
    raw = pool_row["metadata"]
    if isinstance(raw, str):
        try:
            meta: dict = json.loads(raw)
        except (ValueError, TypeError):
            meta = {}
    elif isinstance(raw, dict):
        meta = raw
    else:
        meta = {}

    provider = (pool_row.get("provider") or "aws").lower()
    allowed = list(pool_row.get("allowed_gpu_types") or [])
    instance_type = (allowed[0] if allowed else None) or meta.get("instance_type")
    region = None
    rc = pool_row.get("region_constraint")
    if rc:
        region = (rc[0] if isinstance(rc, (list, tuple)) else rc) or None
    region = region or meta.get("region")
    org_id = pool_row.get("org_id") or pool_row.get("owner_id")

    if not instance_type or not region:
        logger.warning("auto_replica: pool %s missing instance_type or region", pool_id)
        return None

    gpu_count = 1
    # Try to infer gpu_count from an existing ready node
    async with db_pool.acquire() as conn:
        node_row = await conn.fetchrow(
            """
            SELECT gpu_total FROM compute_inventory
             WHERE pool_id = $1 AND state = 'ready'
               AND (metadata->>'terminating') IS DISTINCT FROM 'true'
             LIMIT 1
            """,
            pool_id,
        )
        if node_row:
            gpu_count = node_row["gpu_total"] or 1

    spec = {
        "provider": provider,
        "pool_id": str(pool_id),
        "org_id": str(org_id) if org_id else None,
        "instance_type": instance_type,
        "region": region,
        "gpu_count": gpu_count,
    }

    # Resolve instance_class from AWS catalog
    if provider == "aws":
        from providers.aws.instance_catalog import lookup as _catalog_lookup
        it = _catalog_lookup(instance_type)
        if it is not None:
            spec["instance_class"] = it.cls
        else:
            spec["instance_class"] = "gpu"  # best-effort fallback

    # Copy optional pool-level overrides
    for key in ("subnet_id", "security_group_ids", "security_group_id",
                "iam_instance_profile", "ami_id", "root_volume_gb",
                "worker_image_tag"):
        val = meta.get(key)
        if val not in (None, "", []):
            spec[key] = val

    if "root_volume_gb" not in spec:
        spec["root_volume_gb"] = 130 if spec.get("instance_class") != "cpu" else 30

    return spec


async def tick(
    db_pool,
    deploys_repo,
    jobs_repo,
    inventory_repo,
):
    """One iteration of the auto-replica monitor."""
    deployments = await deploys_repo.list_auto_replica_deployments()
    if not deployments:
        return

    now = _utcnow()

    for dep in deployments:
        dep_id = dep["deployment_id"]
        pool_id = dep["pool_id"]
        threshold = dep.get("tokens_per_second_threshold")
        threshold = float(threshold) if threshold is not None else None
        last_scale = dep.get("auto_replica_last_scale_at")

        if threshold is None:
            continue

        # 1. Cooldown check
        if last_scale is not None:
            if isinstance(last_scale, datetime):
                if now - last_scale < timedelta(minutes=COOLDOWN_MINUTES):
                    continue

        # 2. Compute recent avg tokens_per_second
        avg_tps, req_count = await _recent_avg_tps(db_pool, dep_id)
        if avg_tps is None:
            continue
        if req_count < MIN_REQUESTS:
            continue

        # 3. Below threshold → scale out
        if avg_tps >= threshold:
            continue

        logger.info(
            "auto_replica: deployment %s avg_tps=%.1f < threshold=%.1f "
            "(requests=%d) — scaling pool %s",
            dep_id, avg_tps, threshold, req_count, pool_id,
        )

        # 4. Check pool capacity
        max_nodes = await _pool_max_nodes(db_pool, pool_id)
        ready_nodes = await _pool_ready_nodes(db_pool, pool_id)
        if max_nodes is not None and ready_nodes >= max_nodes:
            logger.info(
                "auto_replica: pool %s at capacity (%d/%d) — skipping",
                pool_id, ready_nodes, max_nodes,
            )
            continue

        # 5. Build provisioning spec
        spec = await _build_pool_spec(db_pool, pool_id)
        if spec is None:
            logger.warning(
                "auto_replica: cannot build spec for pool %s — skipping deployment %s",
                pool_id, dep_id,
            )
            continue

        # 6. Create placeholder node + enqueue provisioning job
        pool_id_uuid = pool_id if isinstance(pool_id, UUID) else UUID(pool_id)
        org_id_str = str(dep.get("org_id") or "") or None

        node_id = await inventory_repo.create_placeholder(
            pool_id=pool_id_uuid,
            gpu_total=spec.get("gpu_count", 1),
            initial_alloc=0,
        )

        job_id = await jobs_repo.enqueue(
            node_id=node_id,
            pool_id=pool_id_uuid,
            org_id=org_id_str or "auto-replica",
            provider=spec.get("provider", "aws"),
            spec=spec,
        )

        # 7. Stamp last_scale_at on the deployment
        await deploys_repo.update_auto_replica(
            dep_id,
            last_scale_at=now,
        )

        logger.info(
            "auto_replica: enqueued provisioning job %s for node %s in pool %s",
            job_id, node_id, pool_id,
        )

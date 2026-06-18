from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import logging
import time as _time
from uuid import UUID


def utcnow_naive():
    return datetime.now(timezone.utc).replace(tzinfo=None)


logger = logging.getLogger("autoscaler")


class Autoscaler:
    def __init__(
        self,
        repo,
        adapter_engine,
        *,
        db_pool=None,
        deploys_repo=None,
        jobs_repo=None,
        inventory_repo=None,
        auto_replica_interval: float = 60.0,
    ):
        self.repo = repo
        self.adapter = adapter_engine

        # Optional auto-replica deps
        self.db_pool = db_pool
        self.deploys_repo = deploys_repo
        self.jobs_repo = jobs_repo
        self.inventory_repo = inventory_repo
        self._last_ar_tick = 0.0
        self._ar_interval = auto_replica_interval

    async def tick(self):
        if self.repo is not None and self.adapter is not None:
            await self._pool_tick()
        await self._auto_replica_tick()

    async def _pool_tick(self):
        """Pool CPU-based autoscaling (scale up/down by CPU util)."""
        try:
            pools = await self.repo.get_pools()
        except Exception as e:
            logger.error("Failed to fetch pools: %s", e)
            return

        for p in pools:
            try:
                policy = json.loads(p["autoscaling_policy"])
                pool_id = p["id"]
            except (json.JSONDecodeError, KeyError) as e:
                logger.warning(
                    "Invalid autoscaling policy for pool %s: %s", p.get("id"), e
                )
                continue

            try:
                stats = await self.repo.pool_stats(pool_id)
                state = await self.repo.state(pool_id)
            except Exception as e:
                logger.error("Failed to get pool stats for %s: %s", pool_id, e)
                continue

            now = utcnow_naive()
            if state["last_scale_at"]:
                if now - state["last_scale_at"] < timedelta(
                    seconds=policy["cooldown_seconds"]
                ):
                    continue

            try:
                # ---------- SCALE UP ----------
                if stats["ready_nodes"] < policy["max_nodes"] and (
                    state["consecutive_failures"] >= 3
                    or (stats["avg_cpu_util"] or 0) >= policy["scale_up_threshold"]
                ):
                    logger.info("Autoscaler: scaling UP pool %s", pool_id)

                    await self.adapter.provision_node(
                        provider=p["provider"],
                        provider_resource_id="default",
                        pool_id=pool_id,
                    )

                    await self.repo.record_scale(pool_id)
                    await self.repo.reset_failures(pool_id)
                    continue

                # ---------- SCALE DOWN ----------
                if (
                    stats["ready_nodes"] > policy["min_nodes"]
                    and (stats["avg_cpu_util"] or 0) <= policy["scale_down_threshold"]
                    and stats["idle_nodes"] > 0
                ):
                    node = await self.repo.find_idle_node(pool_id)
                    if not node:
                        continue

                    logger.info("Autoscaler: draining node %s", node["id"])

                    await self.repo.mark_draining(node["id"])

                    await self.adapter.deprovision_node(
                        provider=node["provider"],
                        provider_instance_id=node["provider_instance_id"],
                    )

                    await self.repo.record_scale(pool_id)
            except Exception as e:
                logger.error("Autoscaler error for pool %s: %s", pool_id, e)
                continue

    async def _auto_replica_tick(self):
        """Deployment TPS-based scale-out, throttled to auto_replica_interval."""
        if not self.db_pool or not self.deploys_repo:
            return
        now = _time.monotonic()
        if now - self._last_ar_tick < self._ar_interval:
            return
        self._last_ar_tick = now

        deployments = await self.deploys_repo.list_auto_replica_deployments()
        if not deployments:
            return

        _now_dt = _utcnow()

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
                    if _now_dt - last_scale < timedelta(minutes=COOLDOWN_MINUTES):
                        continue

            # 2. Compute recent avg tokens_per_second
            avg_tps, req_count = await _recent_avg_tps(self.db_pool, dep_id)
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
            max_nodes = await _pool_max_nodes(self.db_pool, pool_id)
            ready_nodes = await _pool_ready_nodes(self.db_pool, pool_id)
            if max_nodes is not None and ready_nodes >= max_nodes:
                logger.info(
                    "auto_replica: pool %s at capacity (%d/%d) — skipping",
                    pool_id, ready_nodes, max_nodes,
                )
                continue

            # 5. Build provisioning spec
            spec = await _build_pool_spec(self.db_pool, pool_id)
            if spec is None:
                logger.warning(
                    "auto_replica: cannot build spec for pool %s — skipping deployment %s",
                    pool_id, dep_id,
                )
                continue

            # 6. Create placeholder node + enqueue provisioning job
            pool_id_uuid = pool_id if isinstance(pool_id, UUID) else UUID(pool_id)
            org_id_str = str(dep.get("org_id") or "") or None

            node_id = await self.inventory_repo.create_placeholder(
                pool_id=pool_id_uuid,
                gpu_total=spec.get("gpu_count", 1),
                initial_alloc=0,
            )

            job_id = await self.jobs_repo.enqueue(
                node_id=node_id,
                pool_id=pool_id_uuid,
                org_id=org_id_str or "auto-replica",
                provider=spec.get("provider", "aws"),
                spec=spec,
            )

            # 7. Stamp last_scale_at on the deployment
            await self.deploys_repo.update_auto_replica(
                dep_id,
                last_scale_at=_now_dt,
            )

            logger.info(
                "auto_replica: enqueued provisioning job %s for node %s in pool %s",
                job_id, node_id, pool_id,
            )


# =====================================================================
# Module-level helpers for auto-replica
# =====================================================================

COOLDOWN_MINUTES = 10
WINDOW_MINUTES = 5
MIN_REQUESTS = 3


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def _recent_avg_tps(db_pool, deployment_id: UUID) -> tuple[float | None, int]:
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
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT max_nodes FROM compute_pools WHERE id = $1",
            pool_id,
        )
    if row is None:
        return None
    return row["max_nodes"]


async def _build_pool_spec(db_pool, pool_id: UUID) -> dict | None:
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

    if provider == "aws":
        from providers.aws.instance_catalog import lookup as _catalog_lookup
        it = _catalog_lookup(instance_type)
        if it is not None:
            spec["instance_class"] = it.cls
        else:
            spec["instance_class"] = "gpu"

    for key in ("subnet_id", "security_group_ids", "security_group_id",
                "iam_instance_profile", "ami_id", "root_volume_gb",
                "worker_image_tag"):
        val = meta.get(key)
        if val not in (None, "", []):
            spec[key] = val

    if "root_volume_gb" not in spec:
        spec["root_volume_gb"] = 130 if spec.get("instance_class") != "cpu" else 30

    return spec

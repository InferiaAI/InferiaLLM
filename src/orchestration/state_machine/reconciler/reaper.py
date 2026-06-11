"""TerminationReaper — periodic self-healing backstop for teardown.

The deterministic teardown paths (``terminate_deployment_core`` →
``_initiate_node_destroy`` → reconciler ``CancelHandler`` → ``_teardown_node``
→ ``purge_node`` / ``_finalize_pool_if_empty``) cover the common case. But a
crash, an enqueue failure, or any missed edge can leave residue stranded:

  * a NODE flagged ``metadata.terminating='true'`` whose destroy job never
    ran or never finished (no live ``cancelling`` job for it), and
  * a POOL left in ``lifecycle_state='terminating'`` with zero inventory rows
    (its last node was purged but the finalize-on-empty was missed).

This reaper runs alongside the reconciler loop — single-instance guarded by
the SAME Postgres advisory lock the reconciler uses (so only the leader
replica runs it) — and every ``interval_s`` it sweeps for both, re-arming the
real teardown (NODES) or finalizing (POOLS). Every action is idempotent and
best-effort: ``force_cancel`` / ``purge_node`` / ``finalize_pool_delete`` are
all safe to call repeatedly, and a purged node / finalized pool simply does
not reappear on the next tick.

Loop-safety: a node is only acted on once it has been terminating for at
least ``grace_s`` seconds (measured from ``metadata.terminating_at`` when
present, else ``updated_at``), so the reaper never races a destroy that was
initiated microseconds ago. When that timestamp is unavailable the node is
still acted on — ``purge_node`` / ``force_cancel`` are idempotent and a
re-armed/purged node won't keep matching the stuck query.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)


# Phases that mean a destroy is already in flight / finished for a node — the
# reaper must NOT re-arm or sweep these (the CancelHandler is mid-run).
_DESTROY_IN_FLIGHT_PHASES = ("cancelling",)


class TerminationReaper:
    """Periodic backstop that re-arms stuck node destroys and finalizes
    stuck-empty pools.

    Drive one pass with :meth:`tick_once` (used by tests); :meth:`run` loops
    ``tick_once`` every ``interval_s`` until cancelled.
    """

    def __init__(
        self,
        *,
        db: Any,
        inventory_repo: Any,
        pool_repo: Any,
        jobs_repo: Any,
        interval_s: float = 60.0,
        grace_s: float = 120.0,
    ):
        self.db = db
        self.inventory_repo = inventory_repo
        self.pool_repo = pool_repo
        self.jobs_repo = jobs_repo
        self.interval_s = interval_s
        self.grace_s = grace_s

    async def run(self) -> None:
        """Loop ``tick_once`` every ``interval_s`` until cancelled. Each tick
        is fully isolated — a tick that raises is logged and the loop
        continues."""
        while True:
            try:
                await self.tick_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("termination reaper tick raised; continuing")
            await asyncio.sleep(self.interval_s)

    async def tick_once(self) -> None:
        """Run one full reaper pass: nodes first (so a node-purge that empties
        a pool is visible to the pool sweep in the SAME tick), then pools, then
        reclaim any stale engine-AMI builders."""
        await self._reap_stuck_nodes()
        await self._reap_stuck_pools()
        await self._reap_stale_builders()

    # ---- NODES ----------------------------------------------------------

    async def _find_stuck_nodes(self) -> list[dict]:
        """Return inventory rows flagged terminating that have NO destroy in
        flight and have been stuck for at least ``grace_s``.

        A node is 'stuck' when ``metadata.terminating='true'`` AND it has no
        ``provisioning_jobs`` row in a ``cancelling`` phase (the CancelHandler
        only runs on ``cancelling`` — anything else means the destroy is not
        actually being driven). We also carry the node's provider, a
        best-effort region (job spec/outputs → node metadata → pool
        ``region_constraint[0]``), and whether a re-armable job exists.
        """
        sql = """
            WITH latest_job AS (
                SELECT DISTINCT ON (node_id)
                       node_id, phase, spec, pulumi_stack_outputs
                FROM provisioning_jobs
                ORDER BY node_id, created_at DESC
            )
            SELECT
                ci.id                          AS node_id,
                ci.provider::text              AS provider,
                COALESCE(
                    lj.spec->>'region',
                    lj.pulumi_stack_outputs->>'region',
                    ci.metadata->>'region',
                    (cp.region_constraint)[1]
                )                              AS region,
                lj.phase                       AS job_phase,
                COALESCE(
                    (ci.metadata->>'terminating_at')::timestamptz,
                    ci.updated_at
                )                              AS terminating_since
            FROM compute_inventory ci
            LEFT JOIN latest_job lj ON lj.node_id = ci.id
            LEFT JOIN compute_pools cp ON cp.id = ci.pool_id
            WHERE ci.metadata->>'terminating' = 'true'
              AND NOT EXISTS (
                    SELECT 1 FROM provisioning_jobs pj
                    WHERE pj.node_id = ci.id
                      AND pj.phase = ANY($1::text[])
              )
              AND COALESCE(
                    (ci.metadata->>'terminating_at')::timestamptz,
                    ci.updated_at
                  ) <= now() - make_interval(secs => $2)
        """
        async with self.db.acquire() as conn:
            rows = await conn.fetch(
                sql, list(_DESTROY_IN_FLIGHT_PHASES), float(self.grace_s),
            )
        return [dict(r) for r in rows]

    async def _reap_stuck_nodes(self) -> None:
        try:
            stuck = await self._find_stuck_nodes()
        except Exception:
            logger.exception("reaper: find stuck nodes failed; skipping nodes")
            return
        for row in stuck:
            try:
                await self._reap_one_node(row)
            except Exception:
                logger.exception(
                    "reaper: reaping node=%s failed; continuing",
                    row.get("node_id"),
                )

    async def _reap_one_node(self, row: dict) -> None:
        """Re-arm teardown for ONE stuck-terminating node.

        Prefer re-arming via ``force_cancel`` (flips the existing job to
        ``cancelling`` so the CancelHandler runs the authoritative
        ``pulumi destroy`` + ``_teardown_node`` purge). Only when there is
        genuinely no job to re-arm (force_cancel flips nothing) do we fall
        back to the sweep+purge backstop directly.
        """
        node_id = row["node_id"]
        provider = (row.get("provider") or "").lower()
        region = row.get("region")

        # 1. Try to re-arm via force_cancel — flips a non-terminal/non-
        #    cancelling job (preflight/provisioning/bootstrapping/ready/
        #    failed/pending) to 'cancelling'. Returns False when there is no
        #    such job (none at all, or already terminated) → fall back below.
        flipped = False
        if self.jobs_repo is not None and provider:
            try:
                flipped = await self.jobs_repo.force_cancel(node_id=node_id)
            except Exception:
                logger.exception(
                    "reaper: force_cancel(node=%s) failed; falling back to "
                    "sweep+purge", node_id,
                )
                flipped = False
        if flipped:
            logger.info(
                "reaper: re-armed teardown for stuck node=%s via force_cancel",
                node_id,
            )
            return

        # 2. No job to re-arm — run the sweep+purge backstop directly. The
        #    boto3 tag sweep reclaims any orphan EC2 (best-effort, needs a
        #    region); purge_node hard-deletes the row + all DB residue.
        if provider == "aws" and region:
            try:
                from orchestration.provisioning.engine.aws_orphan_sweep import (
                    resolve_sweep_aws_env,
                    sweep_node_instances,
                )
                # Resolve creds HERE on the reaper's loop (asyncpg-bound
                # ProvidersConfig session works) BEFORE the to_thread sweep;
                # resolving inside the worker thread would crash cross-loop and
                # no-op the backstop. Best-effort: None ⇒ no-creds WARNING + [].
                aws_env = await resolve_sweep_aws_env()
                terminated = await asyncio.to_thread(
                    sweep_node_instances, str(node_id), str(region), aws_env,
                )
                if terminated:
                    logger.info(
                        "reaper: orphan sweep terminated %d EC2 for stuck "
                        "node=%s: %s", len(terminated), node_id,
                        ", ".join(terminated),
                    )
            except Exception:
                logger.exception(
                    "reaper: orphan sweep failed for node=%s region=%s; "
                    "continuing with DB purge", node_id, region,
                )
        elif provider == "aws":
            logger.warning(
                "reaper: no region for stuck node=%s; skipping orphan EC2 "
                "sweep (purging DB residue anyway)", node_id,
            )

        if self.inventory_repo is not None:
            await self.inventory_repo.purge_node(node_id)
            logger.info("reaper: purged stuck-terminating node=%s", node_id)

    # ---- BUILDERS -------------------------------------------------------

    async def _aws_regions_in_use(self) -> list[str]:
        """Distinct AWS regions across pools + nodes — the regions a leaked
        engine-AMI builder could be in. (A builder in a region with zero
        pools/nodes relies on the bake's own finally-terminate; the reaper is
        the crash backstop for the common case.)"""
        async with self.db.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT DISTINCT region FROM (
                    SELECT (cp.region_constraint)[1] AS region
                      FROM compute_pools cp WHERE cp.provider::text = 'aws'
                    UNION
                    SELECT ci.metadata->>'region' AS region
                      FROM compute_inventory ci WHERE ci.provider::text = 'aws'
                ) t WHERE region IS NOT NULL AND region <> ''
                """
            )
        return [r["region"] for r in rows]

    async def _reap_stale_builders(self) -> None:
        """Reclaim engine-AMI builder EC2 (tag inferia:engine-ami-builder) leaked
        by a CP crash mid-bake. Best-effort, tag-scoped, age-gated (>30 min in
        sweep_stale_builders) so it never races an in-flight bake."""
        try:
            regions = await self._aws_regions_in_use()
        except Exception:
            logger.exception("reaper: list AWS regions failed; skipping builder sweep")
            return
        if not regions:
            return
        from orchestration.provisioning.engine.aws_orphan_sweep import (
            resolve_sweep_aws_env,
            sweep_stale_builders,
        )
        # Resolve creds on the reaper's loop (asyncpg-bound ProvidersConfig
        # session) BEFORE the to_thread sweep — resolving inside the worker
        # thread crashes cross-loop. Best-effort: None ⇒ no-creds WARNING + [].
        try:
            aws_env = await resolve_sweep_aws_env()
        except Exception:
            logger.exception("reaper: resolve creds for builder sweep failed; skipping")
            return
        for region in regions:
            try:
                terminated = await asyncio.to_thread(
                    sweep_stale_builders, str(region), aws_env,
                )
                if terminated:
                    logger.info(
                        "reaper: reclaimed %d stale engine-AMI builder(s) in %s: %s",
                        len(terminated), region, ", ".join(terminated),
                    )
            except Exception:
                logger.exception(
                    "reaper: stale-builder sweep failed in %s; continuing", region,
                )

    # ---- POOLS ----------------------------------------------------------

    async def _find_stuck_pools(self) -> list[Any]:
        """Return ids of pools in ``lifecycle_state='terminating'`` with ZERO
        ``compute_inventory`` rows — their teardown completed (last node
        purged) but the finalize-on-empty was missed."""
        async with self.db.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT cp.id AS pool_id
                FROM compute_pools cp
                WHERE cp.lifecycle_state = 'terminating'
                  AND NOT EXISTS (
                        SELECT 1 FROM compute_inventory ci
                        WHERE ci.pool_id = cp.id
                  )
                """
            )
        return [r["pool_id"] for r in rows]

    async def _reap_stuck_pools(self) -> None:
        try:
            stuck = await self._find_stuck_pools()
        except Exception:
            logger.exception("reaper: find stuck pools failed; skipping pools")
            return
        if self.pool_repo is None:
            return
        for pool_id in stuck:
            try:
                deleted = await self.pool_repo.finalize_pool_delete(pool_id)
                if deleted:
                    logger.info(
                        "reaper: finalized stuck-terminating empty pool=%s",
                        pool_id,
                    )
            except Exception:
                logger.exception(
                    "reaper: finalize_pool_delete(%s) failed; continuing",
                    pool_id,
                )

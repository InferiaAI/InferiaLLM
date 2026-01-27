from datetime import datetime, timedelta, timezone
import json
import logging

def utcnow_naive():
    return datetime.now(timezone.utc).replace(tzinfo=None)

logger = logging.getLogger("autoscaler")

class Autoscaler:
    def __init__(self, repo, adapter_engine):
        self.repo = repo
        self.adapter = adapter_engine

    async def tick(self):
        pools = await self.repo.get_pools()

        for p in pools:
            policy = json.loads(p["autoscaling_policy"])
            pool_id = p["id"]

            stats = await self.repo.pool_stats(pool_id)
            state = await self.repo.state(pool_id)

            now = utcnow_naive()
            if state["last_scale_at"]:
                if now - state["last_scale_at"] < timedelta(
                    seconds=policy["cooldown_seconds"]
                ):
                    continue

            # ---------- SCALE UP ----------
            if (
                stats["ready_nodes"] < policy["max_nodes"]
                and (
                    state["consecutive_failures"] >= 3
                    or (stats["avg_cpu_util"] or 0)
                    >= policy["scale_up_threshold"]
                )
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
                and (stats["avg_cpu_util"] or 0)
                <= policy["scale_down_threshold"]
                and stats["idle_nodes"] > 0
            ):
                node = await self.repo.find_idle_node(pool_id)
                if not node:
                    continue

                logger.info(
                    "Autoscaler: draining node %s", node["id"]
                )

                await self.repo.mark_draining(node["id"])

                await self.adapter.deprovision_node(
                    provider=node["provider"],
                    provider_instance_id=node["provider_instance_id"],
                )

                await self.repo.record_scale(pool_id)

# services/model_deployment/worker_main.py
import asyncio
import os
import logging
from uuid import UUID
import asyncpg

from inferia.services.orchestration.infra.redis_event_bus import RedisEventBus
from inferia.services.orchestration.services.model_deployment.worker import ModelDeploymentWorker

from inferia.services.orchestration.repositories.model_deployment_repo import ModelDeploymentRepository
from inferia.services.orchestration.repositories.model_registry_repo import ModelRegistryRepository
from inferia.services.orchestration.repositories.pool_repo import ComputePoolRepository
from inferia.services.orchestration.repositories.placement_repo import PlacementRepository
from inferia.services.orchestration.repositories.scheduler_repo import SchedulerRepository
from inferia.services.orchestration.repositories.inventory_repo import InventoryRepository
from inferia.services.orchestration.repositories.quota_repo import QuotaRepository

from inferia.services.orchestration.services.scheduler.service import SchedulerService
from inferia.services.orchestration.services.model_deployment.runtime_resolver import RuntimeResolver
from inferia.services.orchestration.services.model_deployment.strategies.vllm import VLLMDeploymentStrategy
# from services.vllm_runtime.runtime import VLLMRuntime
# from services.nosana_runtime.client import NosanaRuntimeClient


POSTGRES_DSN = os.getenv("POSTGRES_DSN", "postgresql://inferia:inferia@localhost:5432/inferia")
NOSANA_SIDECAR_URL = os.getenv("NOSANA_SIDECAR_URL", "http://localhost:3000/nosana")
POLL_INTERVAL = 30  # seconds

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("deployment-worker")

async def consume_deploy_requests(worker, event_bus):
    async for msg_id, event in event_bus.consume(
        stream="model.deploy.requested",
        group="deployment-workers",
        consumer="worker-1",
    ):
        try:
            log.info(f"Received deploy event: {event}")
            deployment_id = UUID(event["deployment_id"])
            await worker.handle_deploy_requested(deployment_id)
            await event_bus.redis.xack("model.deploy.requested", "deployment-workers", msg_id)
            log.info(f"Successfully processed deploy event for {deployment_id}")
        except Exception:
            log.exception("Failed to process deployment event")
            # do NOT ack → will be retried


async def consume_terminate_requests(worker, event_bus):
    async for msg_id, event in event_bus.consume(
        stream="model.terminate.requested",
        group="deployment-workers",
        consumer="worker-1",
    ):
        try:
            deployment_id = UUID(event["deployment_id"])
            await worker.handle_terminate_requested(deployment_id)
            await event_bus.redis.xack("model.terminate.requested", "deployment-workers", msg_id)
        except Exception:
            log.exception("Failed to process termination event")


async def health_check_loop(inventory_repo, deployment_repo):
    log.info("Starting health check loop")
    while True:
        try:
             # Timeout 120 seconds for stale heartbeat
             stale_ids = await inventory_repo.mark_unhealthy(timeout_seconds=120)

             if stale_ids:
                 log.info(f"Marked {len(stale_ids)} nodes as unhealthy: {stale_ids}")
                 for node_id in stale_ids:
                     deployments = await inventory_repo.get_deployments_for_node(node_id)
                     for d_id in deployments:
                         current_d = await deployment_repo.get(d_id)
                         if current_d and current_d["state"] not in ["TERMINATED", "FAILED", "STOPPED"]:
                             log.info(f"Marking deployment {d_id} as FAILED due to unhealthy node")
                             await deployment_repo.update_state(d_id, "FAILED")

        except Exception:
            log.exception("Error in health check loop")
        
        await asyncio.sleep(POLL_INTERVAL)


async def main():
    # ---------------- DB ----------------
    db_pool = await asyncpg.create_pool(
        dsn=POSTGRES_DSN,
        min_size=2,
        max_size=10,
    )

    # ---------------- Infra ----------------
    event_bus = RedisEventBus()

    # ---------------- Repos ----------------
    deployment_repo = ModelDeploymentRepository(db_pool, event_bus=event_bus)
    model_repo = ModelRegistryRepository(db_pool)
    pool_repo = ComputePoolRepository(db_pool)
    placement_repo = PlacementRepository(db_pool)
    inventory_repo = InventoryRepository(db_pool)
    quota_repo = QuotaRepository(db_pool)
    scheduler_repo = SchedulerRepository(db_pool, quota_repo=quota_repo)

    # ---------------- Services ----------------
    scheduler_service = SchedulerService(
        scheduler_repo=scheduler_repo,
        autoscaler_repo=None,
        job_repo=None,
    )

    runtime_resolver = RuntimeResolver()

    vllm_strategy = VLLMDeploymentStrategy(
        scheduler_repo=scheduler_repo,
    )


    worker = ModelDeploymentWorker(
        deployment_repo=deployment_repo,
        model_registry_repo=model_repo,
        pool_repo=pool_repo,
        placement_repo=placement_repo,
        scheduler=scheduler_repo,
        inventory_repo=inventory_repo,
        runtime_resolver=runtime_resolver,
        runtime_strategies={
            "vllm": vllm_strategy,
        },
    )

    log.info("ModelDeploymentWorker started")

    # while True:
    #     pending = await deployment_repo.list_by_state("PENDING")

    #     for d in pending:
    #         try:
    #             await worker.handle_deploy_requested(d["deployment_id"])
    #         except Exception:
    #             log.exception(
    #                 "Failed deployment %s",
    #                 d["deployment_id"],
    #             )

    #     await asyncio.sleep(POLL_INTERVAL)

    # ---------------- Event Loop ----------------
    await asyncio.gather(
        consume_deploy_requests(worker, event_bus),
        consume_terminate_requests(worker, event_bus),
        health_check_loop(inventory_repo, deployment_repo),
    )
    


if __name__ == "__main__":
    asyncio.run(main())

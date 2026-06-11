# services/model_deployment/worker_main.py
import asyncio
import os
import logging
from uuid import UUID
import asyncpg

from orchestration.infra.redis_event_bus import RedisEventBus
from orchestration.model_deployment.worker import ModelDeploymentWorker

from orchestration.repositories.model_deployment_repo import ModelDeploymentRepository
from orchestration.repositories.model_registry_repo import ModelRegistryRepository
from orchestration.repositories.pool_repo import ComputePoolRepository
from orchestration.repositories.placement_repo import PlacementRepository
from orchestration.repositories.scheduler_repo import SchedulerRepository
from orchestration.repositories.inventory_repo import InventoryRepository
from orchestration.repositories.quota_repo import QuotaRepository
from orchestration.repositories.terminal_log_repo import TerminalLogRepository

from orchestration.scheduler.service import SchedulerService
from orchestration.model_deployment.runtime_resolver import RuntimeResolver
from orchestration.model_deployment.strategies.vllm import VLLMDeploymentStrategy
from orchestration.model_deployment.strategies.localai import LocalAIDeploymentStrategy
from orchestration.model_deployment.strategies.worker import WorkerDeploymentStrategy
from orchestration.worker_controller.registry import WorkerRegistry
from orchestration.worker_controller.controller import WorkerController
# from services.vllm_runtime.runtime import VLLMRuntime
# from services.nosana_runtime.client import NosanaRuntimeClient


def _resolve_postgres_dsn() -> str:
    dsn = os.getenv("POSTGRES_DSN")
    if dsn:
        return dsn
    dsn = os.getenv("DATABASE_URL")
    if dsn:
        return dsn.replace("postgresql+asyncpg://", "postgresql://", 1)
    return "postgresql://inferia:inferia@localhost:5432/inferia"


POSTGRES_DSN = _resolve_postgres_dsn()
NOSANA_SIDECAR_URL = os.getenv("NOSANA_SIDECAR_URL", "http://localhost:3000/nosana")
POLL_INTERVAL = 30  # seconds
MAX_CONCURRENT_DEPLOYS = int(os.getenv("MAX_CONCURRENT_DEPLOYS", "8"))

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("deployment-worker")

async def consume_deploy_requests(worker, event_bus, semaphore):
    async def process_deploy(msg_id, event):
        async with semaphore:
            try:
                log.info(f"Processing deploy event: {event}")
                deployment_id = UUID(event["deployment_id"])
                await worker.handle_deploy_requested(deployment_id)
                await event_bus.redis.xack("model.deploy.requested", "deployment-workers", msg_id)
                log.info(f"Successfully processed deploy event for {deployment_id}")
            except Exception:
                log.exception("Failed to process deployment event")

    async for msg_id, event in event_bus.consume(
        stream="model.deploy.requested",
        group="deployment-workers",
        consumer="worker-1",
    ):
        log.info(f"Received deploy event: {event}. Spawning task.")
        asyncio.create_task(process_deploy(msg_id, event))


async def consume_terminate_requests(worker, event_bus, semaphore):
    async def process_terminate(msg_id, event):
        async with semaphore:
            try:
                log.info(f"Processing terminate event: {event}")
                deployment_id = UUID(event["deployment_id"])
                await worker.handle_terminate_requested(deployment_id)
                await event_bus.redis.xack("model.terminate.requested", "deployment-workers", msg_id)
                log.info(f"Successfully processed terminate event for {deployment_id}")
            except Exception:
                log.exception("Failed to process termination event")

    async for msg_id, event in event_bus.consume(
        stream="model.terminate.requested",
        group="deployment-workers",
        consumer="worker-1",
    ):
        log.info(f"Received terminate event: {event}. Spawning task.")
        asyncio.create_task(process_terminate(msg_id, event))


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
                             await deployment_repo.update_state(
                                 d_id,
                                 "FAILED",
                                 error_message=f"Node {node_id} became unhealthy (heartbeat timeout)",
                             )

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
    terminal_log_repo = TerminalLogRepository(db_pool)
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

    localai_strategy = LocalAIDeploymentStrategy(
        scheduler_repo=scheduler_repo,
    )

    # The worker_main process is a separate background worker from the
    # orchestration HTTP server, so it has its own WorkerRegistry instance.
    # In production this is acceptable because LoadModel commands flow from
    # the model_deployment dispatcher *through* the WS the worker holds with
    # the HTTP server's registry — not this one. The registry here is
    # essentially a placeholder kept in sync via cross-process signalling
    # (shared via Redis pub/sub in a follow-up). For MVP, deployments to
    # worker-kind nodes assume the HTTP server is reachable on the same
    # cluster.
    worker_registry = WorkerRegistry()
    worker_controller = WorkerController(worker_registry)

    worker_strategy = WorkerDeploymentStrategy(
        scheduler_repo=scheduler_repo,
        worker_controller=worker_controller,
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
            "localai": localai_strategy,
            "worker": worker_strategy,
        },
        worker_controller=worker_controller,
    )

    log.info("ModelDeploymentWorker started (max_concurrent=%d)", MAX_CONCURRENT_DEPLOYS)

    # Semaphore caps concurrent in-flight tasks to match DB pool capacity
    deploy_semaphore = asyncio.Semaphore(MAX_CONCURRENT_DEPLOYS)

    # ---------------- Event Loop ----------------
    await asyncio.gather(
        consume_deploy_requests(worker, event_bus, deploy_semaphore),
        consume_terminate_requests(worker, event_bus, deploy_semaphore),
        health_check_loop(inventory_repo, deployment_repo),
    )
    


if __name__ == "__main__":
    asyncio.run(main())

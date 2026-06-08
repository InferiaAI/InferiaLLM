"""
Orchestration Service - Main Entry Point

This is the main entry point for the orchestration layer that includes:
- REST API for deployment management
- gRPC services for compute pool and model management
- Inventory management endpoints
"""

import asyncio
import logging
import os
import asyncpg
import grpc
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

from inferia.common.exception_handlers import register_exception_handlers
from inferia.common.logger import setup_logging
from inferia.common.app_setup import setup_cors, add_standard_health_routes
from inferia.services.orchestration.config import settings

# Configure logging
logger = setup_logging(
    level="INFO",
    service_name="orchestration-service",
    use_json=not settings.is_development,
    logstash_host=settings.logstash_host,
    logstash_port=settings.logstash_port,
    logger_name="inferia.services.orchestration",
)

# Import from absolute paths
from inferia.services.orchestration.services.inventory_manager.http import (
    router as inventory_router,
)
from inferia.services.orchestration.services.model_deployment.deployment_server import (
    router as deployment_engine_router,
)
from inferia.services.orchestration.api import workers as workers_api
from inferia.services.orchestration.api import admin_workers as admin_workers_api
from inferia.services.orchestration.api import admin_engine_ami as admin_engine_ami_api
from inferia.services.orchestration.api import nodes as nodes_api
from inferia.services.orchestration.api import providers as providers_api
from inferia.services.orchestration.services.adapter_engine.registry import (
    ADAPTER_REGISTRY,
)
from inferia.services.orchestration.services.worker_controller.auth import (
    WorkerAuth,
)
from inferia.services.orchestration.services.worker_controller.registry import (
    WorkerRegistry,
)
from inferia.services.orchestration.services.worker_controller.controller import (
    WorkerController,
)

from inferia.services.orchestration.v1 import (
    compute_pool_pb2_grpc,
    model_registry_pb2_grpc,
    model_deployment_pb2_grpc,
)

from inferia.services.orchestration.services.compute_pool_engine.compute_pool_manager import (
    ComputePoolManagerService,
)
from inferia.services.orchestration.services.model_registry.service import (
    ModelRegistryService,
)
from inferia.services.orchestration.services.model_deployment.service import (
    ModelDeploymentService,
)
from inferia.services.orchestration.services.model_deployment.controller import (
    ModelDeploymentController,
)
from inferia.services.orchestration.services.model_deployment.worker import (
    ModelDeploymentWorker,
)
from inferia.services.orchestration.services.model_deployment.runtime_resolver import (
    RuntimeResolver,
)
from inferia.services.orchestration.services.model_deployment.strategies.vllm import (
    VLLMDeploymentStrategy,
)
from inferia.services.orchestration.services.model_deployment.strategies.localai import (
    LocalAIDeploymentStrategy,
)
from inferia.services.orchestration.services.model_deployment.strategies.worker import (
    WorkerDeploymentStrategy,
)
from inferia.services.orchestration.repositories.placement_repo import (
    PlacementRepository,
)
from inferia.services.orchestration.repositories.scheduler_repo import (
    SchedulerRepository,
)
from inferia.services.orchestration.repositories.quota_repo import QuotaRepository

from inferia.services.orchestration.repositories.pool_repo import ComputePoolRepository
from inferia.services.orchestration.repositories.model_registry_repo import (
    ModelRegistryRepository,
)
from inferia.services.orchestration.repositories.model_deployment_repo import (
    ModelDeploymentRepository,
)
from inferia.services.orchestration.repositories.outbox_repo import OutboxRepository
from inferia.services.orchestration.repositories.inventory_repo import (
    InventoryRepository,
)

from inferia.services.orchestration.infra.redis_event_bus import RedisEventBus
from inferia.services.orchestration.middleware import InternalAuthMiddleware
from inferia.services.orchestration.grpc_auth_interceptor import (
    InternalAPIKeyInterceptor,
)

# Model-cache imports — wired after db_pool is available in serve()
from inferia.services.orchestration.services.model_cache import (
    api as mc_api,
    mirror_hf as mc_mirror_hf,
    mirror_ollama as mc_mirror_ollama,
    deps as mc_deps,
    repo as mc_repo,
    paths as mc_paths,
    downloader as mc_downloader,
    eviction as mc_eviction,
)
import httpx as _mc_httpx


async def create_db_pool():
    """Create database connection pool."""
    return await asyncpg.create_pool(
        dsn=settings.postgres_dsn,
        min_size=10,
        max_size=50,
        command_timeout=60,
    )


# Postgres advisory-lock key for the single-active ProvisioningReconciler.
# Generated once and committed verbatim so every replica picks the same lock.
# Postgres advisory locks are per-database; this key is shared across the
# inferia orchestration replicas pointing at the same DB so only one process
# runs the reconciler loop. Postgres auto-releases the lock when the holding
# connection drops, so a crashed inferia-app can't lock out its replacement.
# Advisory lock key for the single-active reconciler.
# Must fit in a signed bigint (Postgres `pg_try_advisory_lock(bigint)`).
# The previous value (0xD1F24B3EC7A91100) overflowed int64 and asyncpg
# silently failed the encode → reconciler crashed before acquiring the
# lock → no provisioning ever ran. Top bit cleared to stay positive.
RECONCILER_LOCK_KEY = 0x51F24B3EC7A91100


async def start_reconciler(
    db,
    *,
    handlers: dict,
    emit_event,
    stop_event: asyncio.Event,
    lease_holder: str,
    poll_for_lock_s: float = 15.0,
    inventory_repo=None,
    load_aws_context=None,
    worker_registry=None,
    pool_repo=None,
) -> None:
    """Single-active reconciler loop.

    Acquires a Postgres advisory lock via ``pg_try_advisory_lock``; if the
    lock is held by another inferia-app instance, sleeps for
    ``poll_for_lock_s`` seconds and retries. Postgres auto-releases the
    lock on connection drop, so a crashed leader can't keep its replicas
    locked out forever.

    Once the lock is held, instantiates a ``ProvisioningReconciler`` and
    runs ``rec.run()`` until ``stop_event`` is set. On shutdown the
    reconciler drains in-flight handlers for up to 30s before cancelling.
    """
    # Local imports keep module import cheap and avoid pulling the
    # reconciler graph into unrelated unit tests.
    from inferia.services.orchestration.services.provisioning.jobs.repository import (
        ProvisioningJobRepository,
    )
    from inferia.services.orchestration.services.provisioning.reconciler.loop import (
        ProvisioningReconciler,
    )
    from inferia.services.orchestration.services.provisioning.reconciler.reaper import (
        TerminationReaper,
    )

    # The self-healing reaper runs alongside the reconciler under the SAME
    # advisory lock (only the leader replica runs it). Interval + grace are
    # env-configurable; set INFERIA_DISABLE_TERMINATION_REAPER=1 to disable
    # it entirely (e.g. tests / operator rollback).
    _reaper_disabled = os.getenv("INFERIA_DISABLE_TERMINATION_REAPER", "0") == "1"
    try:
        _reaper_interval = float(
            os.getenv("INFERIA_TERMINATION_REAPER_INTERVAL_S", "") or 60.0
        )
    except (TypeError, ValueError):
        _reaper_interval = 60.0
    try:
        _reaper_grace = float(
            os.getenv("INFERIA_TERMINATION_REAPER_GRACE_S", "") or 120.0
        )
    except (TypeError, ValueError):
        _reaper_grace = 120.0

    while not stop_event.is_set():
        async with db.acquire() as conn:
            got_lock = await conn.fetchval(
                "SELECT pg_try_advisory_lock($1)", RECONCILER_LOCK_KEY,
            )
            if not got_lock:
                # Another inferia-app holds the lock. Sleep up to
                # poll_for_lock_s, then retry. If stop fires while we
                # wait, exit cleanly.
                try:
                    await asyncio.wait_for(
                        stop_event.wait(), timeout=poll_for_lock_s,
                    )
                except asyncio.TimeoutError:
                    continue
                else:
                    return
            try:
                repo = ProvisioningJobRepository(db)
                rec = ProvisioningReconciler(
                    repo=repo,
                    handlers=handlers,
                    emit_event=emit_event,
                    db=db,
                    concurrency=4,
                    poll_interval_s=2.0,
                    lease_seconds=300,
                    renew_interval_s=60.0,
                    lease_holder=lease_holder,
                    inventory_repo=inventory_repo,
                    load_aws_context=load_aws_context,
                    worker_registry=worker_registry,
                    pool_repo=pool_repo,
                )
                run_task = asyncio.create_task(rec.run())
                # Sibling self-healing reaper — shares this lock's lifetime.
                reaper_task: asyncio.Task | None = None
                if not _reaper_disabled:
                    reaper = TerminationReaper(
                        db=db,
                        inventory_repo=inventory_repo,
                        pool_repo=pool_repo,
                        jobs_repo=repo,
                        interval_s=_reaper_interval,
                        grace_s=_reaper_grace,
                    )
                    reaper_task = asyncio.create_task(reaper.run())
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=None)
                except asyncio.CancelledError:
                    raise
                finally:
                    await rec.stop_with_drain(grace_seconds=30.0)
                    run_task.cancel()
                    try:
                        await run_task
                    except asyncio.CancelledError:
                        pass
                    if reaper_task is not None:
                        reaper_task.cancel()
                        try:
                            await reaper_task
                        except asyncio.CancelledError:
                            pass
            finally:
                await conn.fetchval(
                    "SELECT pg_advisory_unlock($1)", RECONCILER_LOCK_KEY,
                )
        return


async def check_db_health(db_pool):
    """Check database connectivity."""
    try:
        async with db_pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
        return True
    except Exception as e:
        logger.error(f"Database health check failed: {e}")
        return False


async def serve():
    """Main server entry point - starts both HTTP and gRPC servers."""

    # Create gRPC server with API key authentication interceptor
    grpc_auth = InternalAPIKeyInterceptor(settings.internal_api_key)
    server = grpc.aio.server(
        interceptors=[grpc_auth],
        options=[
            ("grpc.max_concurrent_streams", 200),
            ("grpc.max_receive_message_length", 16 * 1024 * 1024),  # 16 MB
            ("grpc.max_send_message_length", 16 * 1024 * 1024),  # 16 MB
        ],
        maximum_concurrent_rpcs=200,
    )

    # Initialize database and event bus
    db_pool = await create_db_pool()
    event_bus = RedisEventBus()

    # ---------------- Repositories ----------------
    inventory_repo = InventoryRepository(db_pool)
    pool_repo = ComputePoolRepository(db_pool)
    outbox_repo = OutboxRepository(db_pool)
    model_registry_repo = ModelRegistryRepository(db_pool)
    model_deployment_repo = ModelDeploymentRepository(
        db=db_pool,
        event_bus=event_bus,
    )

    # ---------------- Worker control plane ----------------
    # The orchestration service sits behind InternalAuthMiddleware, which is
    # the trust boundary with the api_gateway. The api_gateway already
    # enforces user-JWT + RBAC on its admin-API surface, so internal-key-
    # holding callers can be treated as authorised here. The admin_workers
    # router accepts an injectable permission factory so we can tighten this
    # later (e.g. by having the gateway forward a permission claim header).
    #
    # The orchestration Settings model does not declare a jwt_secret_key
    # field, so we read it from env directly (it's the same value the
    # api_gateway loads via its own Settings). Fall back to the internal
    # API key if JWT_SECRET_KEY isn't set — anything ≥32 chars works for
    # signing worker JWTs in MVP.
    _worker_jwt_secret = (
        os.getenv("JWT_SECRET_KEY")
        or getattr(settings, "internal_api_key", "")
        or "inferia-worker-secret-placeholder-please-set-JWT_SECRET_KEY-env-var"
    )
    if len(_worker_jwt_secret) < 32:
        _worker_jwt_secret = (_worker_jwt_secret + "_" * 32)[:32]
    worker_auth = WorkerAuth(
        secret_key=_worker_jwt_secret,
        algorithm=os.getenv("JWT_ALGORITHM", "HS256"),
    )
    worker_registry = WorkerRegistry()
    worker_controller = WorkerController(worker_registry)

    def _permit_all(_perm):
        async def _check(_authorization=None):
            return True
        return _check

    workers_api.configure(
        worker_auth, worker_registry, inventory_repo,
    )
    admin_workers_api.configure(
        worker_auth=worker_auth,
        worker_registry=worker_registry,
        inventory_repo=inventory_repo,
        pool_repo=pool_repo,
        control_plane_external_url=os.getenv(
            "CONTROL_PLANE_EXTERNAL_URL", ""
        ),
        require_permission=_permit_all,
        db_pool=db_pool,
    )
    admin_engine_ami_api.configure(require_permission=_permit_all)

    # /v1/nodes/* — the new node-centric API. Wires only those adapters that
    # implement provision_single_node (Nosana, Akash); the worker adapter is
    # special-cased by the dedicated /add/worker route. k8s adapter is left
    # absent from this map until its single-node path lands.
    nodes_adapters = {}
    for name in ("nosana", "akash"):
        cls = ADAPTER_REGISTRY.get(name)
        if cls is None:
            continue
        try:
            nodes_adapters[name] = cls()
        except Exception as e:
            logger.warning("could not instantiate %s adapter for /v1/nodes: %s", name, e)

    from inferia.services.orchestration.repositories.node_provisioning_repo import (
        NodeProvisioningRepo,
    )
    from inferia.services.orchestration.services.provisioning.jobs.repository import (
        ProvisioningJobRepository,
    )
    # The new state-machine endpoints (POST /add/aws thin enqueue, POST
    # /provisioning/retry, DELETE cancellation, and the job-row read in
    # GET /provisioning) need ProvisioningJobRepository's enqueue /
    # get_by_node / reset_for_retry / request_cancel methods. The legacy
    # phase-summary view (GET /provisioning's phases list, GET
    # /provisioning-logs) still reads from the append-only event log via
    # NodeProvisioningRepo's summarize_phases / current_phase /
    # list_events_after. Wire both side-by-side; api/nodes.py routes each
    # call to the appropriate attribute.
    provisioning_repo = ProvisioningJobRepository(db_pool)
    node_events_repo = NodeProvisioningRepo(inventory_repo.db)

    # /v1/nodes/{id}/ec2-console requires an AWS adapter instance to
    # proxy boto3 console_output fetches. Best-effort instantiation.
    aws_cls = ADAPTER_REGISTRY.get("aws")
    if aws_cls is not None:
        try:
            nodes_adapters["aws"] = aws_cls()
        except Exception as e:
            logger.warning("could not instantiate aws adapter for /v1/nodes: %s", e)

    nodes_api.configure(
        inventory_repo=inventory_repo,
        pool_repo=pool_repo,
        worker_auth=worker_auth,
        control_plane_external_url=os.getenv("CONTROL_PLANE_EXTERNAL_URL", ""),
        adapters=nodes_adapters,
        require_permission=_permit_all,
        provisioning_repo=provisioning_repo,
        node_events_repo=node_events_repo,
        db_pool=db_pool,
    )

    # ---------------- FastAPI App ----------------
    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description="Orchestration Gateway - Compute Pool and Model Deployment Management",
    )

    # CORS configuration (Standardized)
    setup_cors(app, os.getenv("ALLOWED_ORIGINS", ""), settings.is_development)

    # Add internal authentication middleware. The worker control plane
    # endpoints use their own auth (bootstrap JWT for /register, worker JWT
    # for the WS channel) so they're skipped here.
    app.add_middleware(
        InternalAuthMiddleware,
        internal_api_key=settings.internal_api_key,
        skip_paths=[
            "/health",
            "/deployment/ws",
            "/v1/workers/register",
            "/v1/workers/channel",
        ],
    )

    # Register standard exception handlers
    register_exception_handlers(app)

    # Include routers
    app.include_router(inventory_router)
    app.include_router(deployment_engine_router)
    app.include_router(workers_api.router)
    app.include_router(admin_workers_api.router)
    app.include_router(admin_engine_ami_api.router)
    app.include_router(nodes_api.router)
    app.include_router(providers_api.router)

    # ---------------- Model Cache wiring ----------------
    # Instantiate model-cache singletons and wire them into the dep registry.
    _mc_repo_inst = mc_repo.ModelCacheRepo(db_pool)
    _mc_paths_inst = mc_paths.CachePaths(settings.model_cache_dir)
    _mc_http = _mc_httpx.AsyncClient(
        timeout=_mc_httpx.Timeout(connect=10, read=None, write=None, pool=10),
        follow_redirects=True,
    )
    _mc_dl = mc_downloader.DownloadManager(
        repo=_mc_repo_inst,
        paths=_mc_paths_inst,
        http_client=_mc_http,
        settings=settings,
    )

    async def _mc_in_use_model_ids() -> set:
        """Return model_ids referenced by non-terminal deployments (async DB query)."""
        try:
            async with db_pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT DISTINCT inference_model FROM model_deployments "
                    "WHERE state IN ('RUNNING','DEPLOYING','PENDING_NODE') AND inference_model IS NOT NULL"
                )
            return {r["inference_model"] for r in rows}
        except Exception:
            return set()  # fail-safe: if the query fails, evict nothing

    # EvictionManager._in_use must be a SYNC callable.  We keep a module-level
    # cache of the last-fetched set and refresh it at the start of each eviction
    # tick in _mc_eviction_loop below, then patch _mc_evict._in_use to return
    # the fresh snapshot.
    _mc_evict = mc_eviction.EvictionManager(
        repo=_mc_repo_inst,
        paths=_mc_paths_inst,
        max_bytes=settings.model_cache_max_gb * (1024 ** 3),
        in_use=lambda: set(),  # placeholder — refreshed each tick in the loop
    )

    mc_deps.configure(
        repo=_mc_repo_inst,
        paths=_mc_paths_inst,
        settings=settings,
        http_client=_mc_http,
        downloader=_mc_dl,
        eviction=_mc_evict,
    )

    app.include_router(mc_api.router)
    app.include_router(mc_mirror_hf.router)
    app.include_router(mc_mirror_ollama.router)

    # Reset any rows left in 'downloading' by a previous process: their
    # in-flight tasks died with that process and would otherwise stay
    # 'downloading' forever (never finishing, never eviction-eligible).
    try:
        _orphaned = await _mc_repo_inst.reconcile_orphaned_downloads(
            message="download interrupted by control-plane restart; re-add to retry",
        )
        if _orphaned:
            logger.warning(
                "reset %d orphaned model-cache download(s) to error on startup",
                _orphaned,
            )
    except Exception:
        logger.warning("model-cache orphaned-download reconciliation failed", exc_info=True)

    # Share pool with routes
    app.state.pool = db_pool
    app.state.worker_controller = worker_controller
    app.state.event_bus = event_bus

    # Add standard / and /health routes
    add_standard_health_routes(
        app=app,
        app_name=settings.app_name,
        app_version=settings.app_version,
        environment=settings.environment,
    )

    # Note: Dashboard now runs on its own port (3001) via the CLI

    # ---------------- gRPC Services ----------------
    deployment_controller = ModelDeploymentController(
        model_registry_repo=model_registry_repo,
        deployment_repo=model_deployment_repo,
        outbox_repo=outbox_repo,
        event_bus=event_bus,
        pool_repo=pool_repo,
    )
    compute_pool_service = ComputePoolManagerService(
        repo=pool_repo,
        deployment_repo=model_deployment_repo,
        controller=deployment_controller,
    )
    model_registry_service = ModelRegistryService(model_registry_repo)
    model_deployment_service = ModelDeploymentService(controller=deployment_controller)

    # Register gRPC services
    compute_pool_pb2_grpc.add_ComputePoolManagerServicer_to_server(
        compute_pool_service, server
    )
    model_registry_pb2_grpc.add_ModelRegistryServicer_to_server(
        model_registry_service, server
    )
    model_deployment_pb2_grpc.add_ModelDeploymentServiceServicer_to_server(
        model_deployment_service, server
    )

    # ---------------- Start Servers ----------------

    # Start uvicorn (HTTP)
    config = uvicorn.Config(
        app, host=settings.host, port=settings.http_port, log_level="info"
    )
    http_server = uvicorn.Server(config)

    # Add shutdown handler
    shutdown_event = asyncio.Event()

    async def shutdown():
        logger.info("Shutting down servers...")
        shutdown_event.set()
        await event_bus.close()
        await db_pool.close()
        await http_server.shutdown()

    asyncio.create_task(http_server.serve())
    logger.info(f"HTTP server started on port {settings.http_port}")

    # ---------------- Co-located Deployment Dispatcher ----------------
    # The model_deployment dispatcher needs to share this process's
    # WorkerRegistry so that LoadModel commands reach connected workers.
    # We co-locate it here (a separate worker_main process would not see
    # the live WS connections held by this process). Disable with
    # INFERIA_INPROC_DEPLOYMENT_WORKER=0 if you intend to run worker_main
    # standalone via a different bridging mechanism.
    if os.getenv("INFERIA_INPROC_DEPLOYMENT_WORKER", "1") != "0":
        try:
            placement_repo = PlacementRepository(db_pool)
            quota_repo = QuotaRepository(db_pool)
            scheduler_repo = SchedulerRepository(db_pool, quota_repo=quota_repo)
            runtime_resolver = RuntimeResolver()
            vllm_strategy = VLLMDeploymentStrategy(scheduler_repo=scheduler_repo)
            localai_strategy = LocalAIDeploymentStrategy(scheduler_repo=scheduler_repo)
            inproc_worker_strategy = WorkerDeploymentStrategy(
                scheduler_repo=scheduler_repo,
                worker_controller=worker_controller,
            )
            inproc_worker = ModelDeploymentWorker(
                deployment_repo=model_deployment_repo,
                model_registry_repo=model_registry_repo,
                pool_repo=pool_repo,
                placement_repo=placement_repo,
                scheduler=scheduler_repo,
                inventory_repo=inventory_repo,
                runtime_resolver=runtime_resolver,
                runtime_strategies={
                    "vllm": vllm_strategy,
                    "localai": localai_strategy,
                    "worker": inproc_worker_strategy,
                },
                # Lets the gRPC delete path (handle_terminate_requested) unload
                # the model over the live WS channel before destroying the node.
                worker_controller=worker_controller,
            )
            from uuid import UUID as _UUID

            max_concurrent = int(os.getenv("MAX_CONCURRENT_DEPLOYS", "8"))
            deploy_sem = asyncio.Semaphore(max_concurrent)

            async def _consume_deploy_requests():
                async def _process(msg_id, event):
                    async with deploy_sem:
                        try:
                            deployment_id = _UUID(event["deployment_id"])
                            await inproc_worker.handle_deploy_requested(deployment_id)
                            await event_bus.redis.xack(
                                "model.deploy.requested",
                                "deployment-workers",
                                msg_id,
                            )
                        except Exception:
                            logger.exception(
                                "in-proc deploy dispatcher failed"
                            )

                async for msg_id, event in event_bus.consume(
                    stream="model.deploy.requested",
                    group="deployment-workers",
                    consumer="inproc-worker-1",
                ):
                    asyncio.create_task(_process(msg_id, event))

            async def _consume_terminate_requests():
                async def _process(msg_id, event):
                    async with deploy_sem:
                        try:
                            deployment_id = _UUID(event["deployment_id"])
                            await inproc_worker.handle_terminate_requested(deployment_id)
                            await event_bus.redis.xack(
                                "model.terminate.requested",
                                "deployment-workers",
                                msg_id,
                            )
                        except Exception:
                            logger.exception(
                                "in-proc terminate dispatcher failed"
                            )

                async for msg_id, event in event_bus.consume(
                    stream="model.terminate.requested",
                    group="deployment-workers",
                    consumer="inproc-worker-1",
                ):
                    asyncio.create_task(_process(msg_id, event))

            asyncio.create_task(_consume_deploy_requests())
            asyncio.create_task(_consume_terminate_requests())
            logger.info("In-process model deployment dispatcher started")
        except Exception as e:
            logger.warning(
                "Failed to start in-process deployment dispatcher: %s", e
            )

    # ---------------- Provisioning Reconciler ----------------
    # Single-active reconciler loop: advisory-lock-guarded so only one
    # inferia-app replica drives jobs, but every replica boots the loop
    # so the standby polls for the lock and takes over when the leader
    # dies (Postgres auto-releases on connection drop). Disable with
    # INFERIA_DISABLE_PROVISIONING_RECONCILER=1 in any operator-driven
    # rollback scenario.
    reconciler_stop: asyncio.Event | None = None
    reconciler_task: asyncio.Task | None = None
    if os.getenv("INFERIA_DISABLE_PROVISIONING_RECONCILER", "0") != "1":
        try:
            from inferia.services.orchestration.services.provisioning.events import (
                emit_event as _emit_event_to_db,
            )
            from inferia.services.orchestration.services.provisioning.jobs.model import (
                Phase,
            )
            from inferia.services.orchestration.services.provisioning.phases.bootstrap import (
                BootstrapHandler,
            )
            from inferia.services.orchestration.services.provisioning.phases.cancel import (
                CancelHandler,
            )
            from inferia.services.orchestration.services.provisioning.phases.preflight import (
                PreflightHandler,
            )
            from inferia.services.orchestration.services.provisioning.phases.pulumi_up import (
                PulumiUpHandler,
            )

            _provisioning_handlers = {
                Phase.PREFLIGHT: PreflightHandler(),
                Phase.PROVISIONING: PulumiUpHandler(),
                Phase.BOOTSTRAPPING: BootstrapHandler(inventory_repo=inventory_repo),
                Phase.CANCELLING: CancelHandler(),
            }

            async def _emit_event(**kwargs):
                # Reconciler emits with positional-by-name args matching
                # events.emit_event's signature (pool_id, node_id, phase,
                # status, message, extra). Adapter forwards the db pool.
                await _emit_event_to_db(db_pool, **kwargs)

            async def _load_aws_context(job):
                # Resolve AWS creds + the Pulumi env for a provisioning job.
                # Without this, the reconciler injects aws_creds=None into
                # PhaseContext and PreflightHandler.verify_credentials(None)
                # crashes, while pulumi up runs with no AWS env. Loads the
                # account-wide ProvidersConfig (Settings -> Providers -> AWS)
                # and returns (AWSCredentials, env_dict). Returns (None, {})
                # for non-AWS providers (GCP/Azure carry their own env via
                # the legacy direct-adapter path).
                provider = (getattr(job, "provider", None) or "").lower()
                if provider and provider != "aws":
                    return None, {}
                from inferia.services.orchestration.services.adapter_engine.adapters.pulumi.pulumi_aws_adapter import (
                    load_providers_config,
                )
                from inferia.services.orchestration.services.adapter_engine.adapters.pulumi.credentials import (
                    AWSCredentials, MissingCredentialsError, resolve_aws_env,
                )
                try:
                    cfg = await load_providers_config()
                    env = resolve_aws_env(cfg)
                except MissingCredentialsError as e:
                    # Creds not configured (Settings -> Providers -> AWS).
                    # Return (None, {}) so PreflightHandler fails the job
                    # cleanly with an actionable message instead of crashing
                    # the reconciler worker loop in a tight retry.
                    logger.warning("load_aws_context: AWS creds unavailable: %s", e)
                    return None, {}
                creds = AWSCredentials(
                    access_key_id=env["AWS_ACCESS_KEY_ID"],
                    secret_access_key=env["AWS_SECRET_ACCESS_KEY"],
                    region=env["AWS_DEFAULT_REGION"],
                )
                return creds, env

            import socket as _socket  # local import keeps the optional dep narrow

            reconciler_stop = asyncio.Event()
            reconciler_task = asyncio.create_task(
                start_reconciler(
                    db=db_pool,
                    handlers=_provisioning_handlers,
                    emit_event=_emit_event,
                    stop_event=reconciler_stop,
                    lease_holder=(
                        f"inferia-app-{os.getpid()}-{_socket.gethostname()}"
                    ),
                    inventory_repo=inventory_repo,
                    load_aws_context=_load_aws_context,
                    worker_registry=worker_registry,
                    pool_repo=pool_repo,
                )
            )
            app.state.reconciler_stop = reconciler_stop
            app.state.reconciler_task = reconciler_task
            logger.info("Provisioning reconciler started (advisory-lock guarded)")
        except Exception as e:
            logger.warning(
                "Failed to start provisioning reconciler: %s", e
            )

    # ---------------- Model-cache eviction loop ----------------
    # Runs once per minute; refreshes the in-use snapshot from the DB
    # (async), then invokes a synchronous eviction pass.  Disabled via
    # INFERIA_DISABLE_MODEL_CACHE_EVICTION=1 for local dev / unit tests.
    if os.getenv("INFERIA_DISABLE_MODEL_CACHE_EVICTION", "0") != "1":
        async def _mc_eviction_loop():
            while True:
                try:
                    ids = await _mc_in_use_model_ids()
                    # Patch the sync callable each tick with the fresh snapshot.
                    _mc_evict._in_use = lambda ids=ids: ids
                    await _mc_evict.run_once()
                except Exception:
                    logger.warning("model-cache eviction tick failed", exc_info=True)
                await asyncio.sleep(60)

        _mc_eviction_task = asyncio.create_task(_mc_eviction_loop())
        app.state.mc_eviction_task = _mc_eviction_task
        logger.info("Model-cache eviction loop started")

    # Start gRPC
    server.add_insecure_port(f"[::]:{settings.grpc_port}")
    await server.start()
    logger.info(f"gRPC server started on port {settings.grpc_port}")

    # Wait for shutdown signal
    await shutdown_event.wait()

    # Graceful shutdown — signal the reconciler first so its in-flight
    # handlers get the full 30s drain budget before we tear down gRPC + DB.
    if reconciler_stop is not None:
        reconciler_stop.set()
    if reconciler_task is not None:
        try:
            await asyncio.wait_for(reconciler_task, timeout=35.0)
        except asyncio.TimeoutError:
            logger.warning("reconciler shutdown timed out; cancelling")
            reconciler_task.cancel()
            try:
                await reconciler_task
            except (asyncio.CancelledError, Exception):
                pass

    # Cancel the model-cache eviction loop and close its httpx client.
    if getattr(app.state, "mc_eviction_task", None) is not None:
        app.state.mc_eviction_task.cancel()
    try:
        await _mc_http.aclose()
    except Exception:
        pass

    await server.stop(grace=5)
    logger.info("Servers stopped gracefully")


if __name__ == "__main__":
    logger.info(f"Starting {settings.app_name} v{settings.app_version}")
    asyncio.run(serve())

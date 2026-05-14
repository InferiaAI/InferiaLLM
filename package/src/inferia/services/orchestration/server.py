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
from inferia.services.orchestration.api import nodes as nodes_api
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


async def create_db_pool():
    """Create database connection pool."""
    return await asyncpg.create_pool(
        dsn=settings.postgres_dsn,
        min_size=10,
        max_size=50,
        command_timeout=60,
    )


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
    )

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
    nodes_api.configure(
        inventory_repo=inventory_repo,
        pool_repo=pool_repo,
        worker_auth=worker_auth,
        control_plane_external_url=os.getenv("CONTROL_PLANE_EXTERNAL_URL", ""),
        adapters=nodes_adapters,
        require_permission=_permit_all,
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
    app.include_router(nodes_api.router)

    # Share pool with routes
    app.state.pool = db_pool

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

    # Start gRPC
    server.add_insecure_port(f"[::]:{settings.grpc_port}")
    await server.start()
    logger.info(f"gRPC server started on port {settings.grpc_port}")

    # Wait for shutdown signal
    await shutdown_event.wait()

    # Graceful shutdown
    await server.stop(grace=5)
    logger.info("Servers stopped gracefully")


if __name__ == "__main__":
    logger.info(f"Starting {settings.app_name} v{settings.app_version}")
    asyncio.run(serve())

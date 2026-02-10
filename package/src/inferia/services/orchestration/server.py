"""
Orchestration Service - Main Entry Point

This is the main entry point for the orchestration layer that includes:
- REST API for deployment management
- gRPC services for compute pool and model management
- Inventory management endpoints
"""

import asyncio
import logging
import asyncpg
import grpc
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

from inferia.services.orchestration.config import settings

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("orchestration-service")

# Import from absolute paths
from inferia.services.orchestration.services.inventory_manager.http import (
    router as inventory_router,
)
from inferia.services.orchestration.services.model_deployment.deployment_server import (
    router as deployment_engine_router,
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


async def create_db_pool():
    """Create database connection pool."""
    return await asyncpg.create_pool(
        dsn=settings.postgres_dsn,
        min_size=10,
        max_size=50,
        command_timeout=30,
    )


async def serve():
    """Main server entry point - starts both HTTP and gRPC servers."""

    # Create gRPC server
    server = grpc.aio.server(
        options=[
            ("grpc.max_concurrent_streams", 10000),
        ]
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

    # ---------------- FastAPI App ----------------
    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description="Orchestration Gateway - Compute Pool and Model Deployment Management",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Include routers
    app.include_router(inventory_router)
    app.include_router(deployment_engine_router)

    # Share pool with routes
    app.state.pool = db_pool

    # Health check endpoint
    @app.get("/health")
    async def health_check():
        return {
            "status": "healthy",
            "service": settings.app_name,
            "version": settings.app_version,
        }

    # Note: Dashboard now runs on its own port (3001) via the CLI

    # ---------------- gRPC Services ----------------
    compute_pool_service = ComputePoolManagerService(pool_repo)
    model_registry_service = ModelRegistryService(model_registry_repo)
    model_deployment_service = ModelDeploymentService(
        controller=ModelDeploymentController(
            model_registry_repo=model_registry_repo,
            deployment_repo=model_deployment_repo,
            outbox_repo=outbox_repo,
            event_bus=event_bus,
        )
    )

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
    asyncio.create_task(http_server.serve())
    logger.info(f"HTTP server started on port {settings.http_port}")

    # Start gRPC
    server.add_insecure_port(f"[::]:{settings.grpc_port}")
    await server.start()
    logger.info(f"gRPC server started on port {settings.grpc_port}")

    await server.wait_for_termination()


if __name__ == "__main__":
    logger.info(f"Starting {settings.app_name} v{settings.app_version}")
    asyncio.run(serve())

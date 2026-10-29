"""ModelDeployment gRPC path (service -> controller -> repos) against a real Postgres.

Stops at the control plane: the deployment row, its outbox entry and the worker event.
Driving it to ready needs a worker; see e2e/manual_e2e_control_plane.py.
"""
from uuid import UUID, uuid4

import grpc
import pytest
import pytest_asyncio

from orchestration.models.model_deployment.controller import ModelDeploymentController
from orchestration.models.model_deployment.service import ModelDeploymentService
from orchestration.repositories.model_deployment_repo import ModelDeploymentRepository
from orchestration.repositories.model_registry_repo import ModelRegistryRepository
from orchestration.repositories.outbox_repo import OutboxRepository
from orchestration.repositories.pool_repo import ComputePoolRepository
from orchestration.v1 import model_deployment_pb2, model_deployment_pb2_grpc
from tests.orchestration.infra.setup_helpers import create_test_pool

pytestmark = pytest.mark.asyncio


class _SpyEventBus:
    def __init__(self):
        self.published = []

    async def publish(self, topic, payload=None):
        self.published.append((topic, payload))


@pytest_asyncio.fixture
async def event_bus():
    return _SpyEventBus()


@pytest_asyncio.fixture
async def stub(db_pool, grpc_channel, event_bus):
    controller = ModelDeploymentController(
        model_registry_repo=ModelRegistryRepository(db_pool),
        deployment_repo=ModelDeploymentRepository(db=db_pool, event_bus=event_bus),
        outbox_repo=OutboxRepository(db_pool),
        event_bus=event_bus,
        pool_repo=ComputePoolRepository(db_pool),
    )
    channel = await grpc_channel(
        (
            model_deployment_pb2_grpc.add_ModelDeploymentServiceServicer_to_server,
            ModelDeploymentService(controller=controller),
        )
    )
    return model_deployment_pb2_grpc.ModelDeploymentServiceStub(channel)


async def test_deploy_creates_pending_deployment(db_pool, stub, event_bus):
    pool_id = await create_test_pool(db_pool)
    model_name = f"llama-{uuid4().hex[:8]}"

    deploy = await stub.DeployModel(
        model_deployment_pb2.DeployModelRequest(
            model_name=model_name,
            model_version="v1",
            pool_id=str(pool_id),
            replicas=1,
            gpu_per_replica=0,
            workload_type="inference",
        )
    )
    assert deploy.state == "PENDING"
    deployment_id = UUID(deploy.deployment_id)

    got = await stub.GetDeployment(
        model_deployment_pb2.GetDeploymentRequest(deployment_id=str(deployment_id))
    )
    assert got.pool_id == str(pool_id)
    assert got.state == "PENDING"
    assert got.replicas == 1

    outbox_events = await db_pool.fetchval(
        "SELECT count(*) FROM outbox_events WHERE aggregate_id = $1",
        deployment_id,
    )
    assert outbox_events == 1
    topics = [t for t, p in event_bus.published if p and p.get("deployment_id") == str(deployment_id)]
    assert "model.deploy.requested" in topics


async def test_get_unknown_deployment_is_not_found(stub):
    with pytest.raises(grpc.aio.AioRpcError) as exc:
        await stub.GetDeployment(
            model_deployment_pb2.GetDeploymentRequest(deployment_id=str(uuid4()))
        )
    assert exc.value.code() == grpc.StatusCode.NOT_FOUND

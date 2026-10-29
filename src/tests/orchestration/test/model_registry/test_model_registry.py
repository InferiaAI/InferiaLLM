"""ModelRegistry gRPC service against a real Postgres, served in-process."""
import json
from uuid import uuid4

import grpc
import pytest
import pytest_asyncio

from orchestration.models.model_registry.service import ModelRegistryService
from orchestration.repositories.model_registry_repo import ModelRegistryRepository
from orchestration.v1 import model_registry_pb2, model_registry_pb2_grpc

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def stub(db_pool, grpc_channel):
    service = ModelRegistryService(ModelRegistryRepository(db_pool))
    channel = await grpc_channel(
        (model_registry_pb2_grpc.add_ModelRegistryServicer_to_server, service)
    )
    return model_registry_pb2_grpc.ModelRegistryStub(channel)


async def test_register_get_and_list(stub):
    name = f"llama-{uuid4().hex[:8]}"
    registered = await stub.RegisterModel(
        model_registry_pb2.RegisterModelRequest(
            name=name,
            version="v1",
            backend="vllm",
            artifact_uri=f"hf://meta-llama/{name}",
            config_json=json.dumps({"tensor_parallel": 2}),
        )
    )
    assert registered.model_id

    model = await stub.GetModel(
        model_registry_pb2.GetModelRequest(name=name, version="v1")
    )
    assert model.model_id == registered.model_id
    assert model.backend == "vllm"
    assert json.loads(model.config_json) == {"tensor_parallel": 2}

    listed = await stub.ListModels(model_registry_pb2.ListModelsRequest(name=name))
    assert [m.model_id for m in listed.models] == [registered.model_id]


async def test_get_unknown_model_is_not_found(stub):
    with pytest.raises(grpc.aio.AioRpcError) as exc:
        await stub.GetModel(
            model_registry_pb2.GetModelRequest(name=f"missing-{uuid4().hex}", version="v1")
        )
    assert exc.value.code() == grpc.StatusCode.NOT_FOUND


async def test_unsupported_backend_is_invalid_argument(stub):
    with pytest.raises(grpc.aio.AioRpcError) as exc:
        await stub.RegisterModel(
            model_registry_pb2.RegisterModelRequest(
                name=f"bad-{uuid4().hex[:8]}", version="v1", backend="not-a-backend",
                artifact_uri="hf://x/y",
            )
        )
    assert exc.value.code() == grpc.StatusCode.INVALID_ARGUMENT

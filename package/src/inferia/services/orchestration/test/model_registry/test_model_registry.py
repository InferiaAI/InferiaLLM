import asyncio
import grpc
import json

from v1 import model_registry_pb2, model_registry_pb2_grpc


async def test_model_registry():
    channel = grpc.aio.insecure_channel("localhost:50051")
    stub = model_registry_pb2_grpc.ModelRegistryStub(channel)

    print("== Register model ==")
    r = await stub.RegisterModel(
        model_registry_pb2.RegisterModelRequest(
            name="llama-3-12b",
            version="v1",
            backend="vllm",
            artifact_uri="hf://meta-llama/llama-3-12b",
            config_json=json.dumps({"tensor_parallel": 2}),
        )
    )

    print("Model ID:", r.model_id)

    print("== Get model ==")
    m = await stub.GetModel(
        model_registry_pb2.GetModelRequest(
            name="llama-3-12b",
            version="v1",
        )
    )
    print("Model:", m)
    assert m.backend == "vllm"

    print("== List models ==")
    lst = await stub.ListModels(
        model_registry_pb2.ListModelsRequest()
    )
    print("Models:", lst.models)
    assert len(lst.models) >= 1

    print("✔ Model Registry test passed")


if __name__ == "__main__":
    asyncio.run(test_model_registry())

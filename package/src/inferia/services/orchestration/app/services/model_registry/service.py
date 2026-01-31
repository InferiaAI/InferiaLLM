import json
from uuid import UUID

from v1 import model_registry_pb2, model_registry_pb2_grpc


ALLOWED_BACKENDS = {"vllm", "trt", "gguf"}


class ModelRegistryService(
    model_registry_pb2_grpc.ModelRegistryServicer
):
    def __init__(self, repo):
        self.repo = repo

    async def RegisterModel(self, request, context):
        if request.backend not in ALLOWED_BACKENDS:
            context.abort(
                code=3,  # INVALID_ARGUMENT
                details=f"Unsupported backend: {request.backend}",
            )

        config = (
            json.loads(request.config_json)
            if request.config_json
            else None
        )

        model_id = await self.repo.register_model(
            name=request.name,
            version=request.version,
            backend=request.backend,
            artifact_uri=request.artifact_uri,
            config=config,
        )

        return model_registry_pb2.RegisterModelResponse(
            model_id=str(model_id)
        )

    async def GetModel(self, request, context):
        model = await self.repo.get_model(
            request.name, request.version
        )
        if not model:
            context.abort(
                code=5,  # NOT_FOUND
                details="Model not found",
            )

        return model_registry_pb2.GetModelResponse(
            model_id=str(model["model_id"]),
            name=model["name"],
            version=model["version"],
            backend=model["backend"],
            artifact_uri=model["artifact_uri"],
            config_json=json.dumps(model["config"] or {}),
        )

    async def ListModels(self, request, context):
        models = await self.repo.list_models(
            name=request.name or None
        )

        return model_registry_pb2.ListModelsResponse(
            models=[
                model_registry_pb2.GetModelResponse(
                    model_id=str(m["model_id"]),
                    name=m["name"],
                    version=m["version"],
                    backend=m["backend"],
                    artifact_uri=m["artifact_uri"],
                    config_json=json.dumps(m["config"] or {}),
                )
                for m in models
            ]
        )

    async def DeleteModel(self, request, context):
        await self.repo.delete_model(UUID(request.model_id))
        return model_registry_pb2.Empty()

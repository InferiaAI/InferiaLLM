from uuid import UUID
import grpc
import json
import logging

logger = logging.getLogger(__name__)
from inferia.services.orchestration.v1 import (
    model_deployment_pb2,
    model_deployment_pb2_grpc,
)


def parse_uuid(value: str, field: str, context):
    try:
        return UUID(value)
    except Exception:
        context.abort(
            grpc.StatusCode.INVALID_ARGUMENT,
            f"Invalid UUID for field '{field}'",
        )


class ModelDeploymentService(
    model_deployment_pb2_grpc.ModelDeploymentServiceServicer
):
    def __init__(self, controller):
        self.controller = controller

    # -------------------------------------------------
    # DEPLOY
    # -------------------------------------------------
    async def DeployModel(self, request, context):
        pool_id = parse_uuid(request.pool_id, "pool_id", context)

        deployment_id = await self.controller.deploy_model(
            model_name=request.model_name,
            model_version=request.model_version,
            pool_id=pool_id,
            replicas=request.replicas,
            gpu_per_replica=request.gpu_per_replica,
            workload_type=request.workload_type or "inference",
            # Unified fields
            engine=request.engine if request.engine else None,
            configuration=request.configuration if request.configuration else None,
            owner_id=request.owner_id if request.owner_id else None,
            endpoint=request.endpoint if request.endpoint else None,
            org_id=request.org_id if request.org_id else None,
            policies=request.policies if request.policies else None,
            inference_model=request.inference_model if request.inference_model else None,
        )

        return model_deployment_pb2.DeployModelResponse(
            deployment_id=str(deployment_id),
            state="PENDING",
        )

    async def GetDeployment(self, request, context):
        logger.info(f"GetDeployment requested: {request.deployment_id}")
        deployment_id = parse_uuid(
            request.deployment_id, "deployment_id", context
        )

        logger.info(f"Fetching deployment {deployment_id} from controller")
        d = await self.controller.get_deployment(deployment_id)
        if not d:
            logger.warning(f"Deployment {deployment_id} not found")
            context.abort(
                grpc.StatusCode.NOT_FOUND,
                "Deployment not found",
            )
        logger.info(f"Found deployment: {d.get('deployment_id')}")

        return model_deployment_pb2.GetDeploymentResponse(
            deployment_id=str(d["deployment_id"]),
            model_name=d.get("model_name") or str(d.get("model_id") or ""),
            model_version=d.get("model_version", ""),
            replicas=d["replicas"],
            gpu_per_replica=d["gpu_per_replica"],
            pool_id=str(d["pool_id"]) if d.get("pool_id") else "",
            state=d["state"],
            engine=d.get("engine") or "",
            configuration=json.dumps(d.get("configuration")) if isinstance(d.get("configuration"), (dict, list)) else (d.get("configuration") or "{}"),
            owner_id=d.get("owner_id") or "",
            endpoint=d.get("endpoint") or "",
            org_id=d.get("org_id") or "",
            policies=json.dumps(d.get("policies")) if isinstance(d.get("policies"), (dict, list)) else (d.get("policies") or "{}"),
            inference_model=d.get("inference_model") or "",
        )

    # -------------------------------------------------
    # LIST
    # -------------------------------------------------
    async def ListDeployments(self, request, context):
        logger.info(f"ListDeployments requested: pool_id='{request.pool_id}', org_id='{request.org_id}'")
        try:
            pool_id = parse_uuid(request.pool_id, "pool_id", context) if request.pool_id else None
            org_id = request.org_id if request.org_id else None
            
            logger.info(f"Calling controller.list_deployments(pool_id={pool_id}, org_id={org_id})")
            deployments = await self.controller.list_deployments(
                pool_id=pool_id,
                org_id=org_id,
            )
            logger.info(f"Controller returned {len(deployments)} deployments")
        except Exception as e:
            logger.error(f"Error in ListDeployments: {str(e)}", exc_info=True)
            await context.abort(grpc.StatusCode.INTERNAL, str(e))
            return

        return model_deployment_pb2.ListDeploymentsResponse(
            deployments=[
                model_deployment_pb2.GetDeploymentResponse(
                    deployment_id=str(d["deployment_id"]),
                    model_name=d.get("model_name") or str(d.get("model_id") or ""),
                    model_version=d.get("model_version") or "latest",
                    replicas=d["replicas"],
                    gpu_per_replica=d["gpu_per_replica"],
                    pool_id=str(d["pool_id"]) if d.get("pool_id") else "",
                    state=d["state"],
                    engine=d.get("engine") or "",
                    configuration=json.dumps(d.get("configuration")) if isinstance(d.get("configuration"), (dict, list)) else (d.get("configuration") or "{}"),
                    owner_id=d.get("owner_id") or "",
                    endpoint=d.get("endpoint") or "",
                    org_id=d.get("org_id") or "",
                    policies=json.dumps(d.get("policies")) if isinstance(d.get("policies"), (dict, list)) else (d.get("policies") or "{}"),
                    inference_model=d.get("inference_model") or "",
                )
                for d in deployments
            ]
        )

    # -------------------------------------------------
    # START
    # -------------------------------------------------
    async def StartDeployment(self, request, context):
        deployment_id = parse_uuid(
            request.deployment_id, "deployment_id", context
        )

        try:
            await self.controller.start_deployment(deployment_id)
        except Exception as e:
            import logging
            logging.getLogger(__name__).exception("Failed to start deployment")
            await context.abort(grpc.StatusCode.INTERNAL, str(e))

        return model_deployment_pb2.StartDeploymentResponse(
            accepted=True,
            state="PENDING"
        )

    # -------------------------------------------------
    # DELETE
    # -------------------------------------------------
    async def DeleteDeployment(self, request, context):
        deployment_id = parse_uuid(
            request.deployment_id, "deployment_id", context
        )

        await self.controller.request_delete(deployment_id)

        return model_deployment_pb2.DeleteDeploymentResponse(
            accepted=True
        )

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import asyncpg
import grpc
import json

from inferia.services.orchestration.v1 import (
    model_deployment_pb2,
    model_deployment_pb2_grpc,
    model_registry_pb2,
    model_registry_pb2_grpc,
    compute_pool_pb2,
    compute_pool_pb2_grpc,
)

from inferia.services.orchestration.repositories.provider_repo import (
    ProviderResourceRepository,
)
from inferia.services.orchestration.services.adapter_engine.registry import get_adapter

import os

POSTGRES_DSN = os.getenv(
    "POSTGRES_DSN", "postgresql://inferia:inferia@localhost:5432/inferia"
)
GRPC_ADDR = "127.0.0.1:50051"

router = APIRouter(prefix="/deployment", tags=["Deployment"])


# status start stop


class DeployModelRequest(BaseModel):
    model_name: str
    model_version: str
    replicas: int
    gpu_per_replica: int
    workload_type: str = "inference"
    pool_id: str
    job_definition: dict | None = None

    # Unified fields
    engine: str | None = None
    configuration: dict | None = None
    owner_id: str | None = None
    endpoint: str | None = None
    org_id: str | None = None
    policies: dict | None = None
    inference_model: str | None = None
    model_type: str = "inference"  # inference, embedding, image_generation, etc.


class TerminateDeploymentRequest(BaseModel):
    deployment_id: str


class CreatePoolRequest(BaseModel):
    pool_name: str
    owner_type: str
    owner_id: str
    provider: str
    allowed_gpu_types: list[str]
    max_cost_per_hour: float
    is_dedicated: bool
    provider_pool_id: str
    scheduling_policy_json: str
    provider_credential_name: str | None = (
        None  # Generic: which credential to use for this provider
    )


class ModelRegistryRequest(BaseModel):
    model_name: str
    model_version: str
    backend: str
    artifact_uri: str
    config_json: dict


class DeleteModelRequest(BaseModel):
    model_id: str


# Audit Helper
def utcnow_naive():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).replace(tzinfo=None)


async def log_audit_event(
    user_id: str | None,
    action: str,
    resource_type: str,
    resource_id: str | None,
    details: dict | None = None,
    status: str = "success",
):
    import uuid

    try:
        conn = await asyncpg.connect(POSTGRES_DSN)
        try:
            # Manually insert since we don't have the AuditLog model here
            await conn.execute(
                """
                INSERT INTO audit_logs (id, timestamp, user_id, action, resource_type, resource_id, details, status)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
             """,
                str(uuid.uuid4()),
                utcnow_naive(),
                user_id,
                action,
                resource_type,
                resource_id,
                json.dumps(details) if details else None,
                status,
            )
        finally:
            await conn.close()
    except Exception as e:
        print(f"Failed to write audit log: {e}")


@router.post("/deploy")
async def deploy_model(req: DeployModelRequest):
    async with grpc.aio.insecure_channel(GRPC_ADDR) as channel:
        stub = model_deployment_pb2_grpc.ModelDeploymentServiceStub(channel)

        # pool_id = uuid.uuid4() # No longer generating random pool_id

        try:
            resp = await stub.DeployModel(
                model_deployment_pb2.DeployModelRequest(
                    model_name=req.model_name,
                    model_version=req.model_version,
                    pool_id=req.pool_id,  # Using pool_id from request
                    replicas=req.replicas,
                    gpu_per_replica=req.gpu_per_replica,
                    workload_type=req.workload_type,
                    engine=req.engine,
                    configuration=json.dumps(
                        req.configuration or req.job_definition or {}
                    ),
                    owner_id=req.owner_id,
                    endpoint=req.endpoint,
                    org_id=req.org_id,
                    policies=json.dumps(req.policies) if req.policies else None,
                    inference_model=req.inference_model,
                    model_type=req.model_type,
                )
            )

            # Log Audit Event
            await log_audit_event(
                user_id=req.owner_id,
                action="deployment.create",
                resource_type="deployment",
                resource_id=resp.deployment_id,
                details={
                    "name": req.model_name,
                    "model": req.inference_model or req.model_version,
                    "pool_id": req.pool_id,
                },
            )

        except grpc.RpcError as e:
            raise HTTPException(
                status_code=500,
                detail=f"Deployment failed: {e.details()}",
            )

    return {
        "deployment_id": resp.deployment_id,
        "status": "DEPLOYING",
    }


@router.get("/status/{deployment_id}")
async def get_deployment_status(deployment_id: str):
    async with grpc.aio.insecure_channel(GRPC_ADDR) as channel:
        stub = model_deployment_pb2_grpc.ModelDeploymentServiceStub(channel)

        try:
            resp = await stub.GetDeployment(
                model_deployment_pb2.GetDeploymentRequest(deployment_id=deployment_id)
            )
        except grpc.RpcError as e:
            raise HTTPException(
                status_code=404,
                detail=f"Deployment not found: {e.details()}",
            )

    return {
        "deployment_id": resp.deployment_id,
        "state": resp.state,
        "replicas": resp.replicas,
        "pool_id": resp.pool_id,
        "model_name": resp.model_name,
        "model_version": resp.model_version,
        "configuration": json.loads(resp.configuration) if resp.configuration else {},
        "owner_id": resp.owner_id,
        "endpoint": resp.endpoint,
        "org_id": resp.org_id,
        "policies": json.loads(resp.policies) if resp.policies else {},
        "engine": resp.engine,
        "inference_model": resp.inference_model,
    }


@router.post("/terminate")
async def terminate_deployment(req: TerminateDeploymentRequest):
    async with grpc.aio.insecure_channel(GRPC_ADDR) as channel:
        stub = model_deployment_pb2_grpc.ModelDeploymentServiceStub(channel)

        try:
            await stub.DeleteDeployment(
                model_deployment_pb2.DeleteDeploymentRequest(
                    deployment_id=req.deployment_id
                )
            )
            # Cannot easily log user_id here as request doesn't contain it, assuming system or fetching logic needed.
            # Skipping audit for terminate here to focus on Create reqs.
        except grpc.RpcError as e:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to terminate deployment: {e.details()}",
            )

    return {
        "deployment_id": req.deployment_id,
        "status": "TERMINATED",
    }


@router.post("/start")
async def start_deployment(
    req: TerminateDeploymentRequest,
):  # Reusing same request body structure
    async with grpc.aio.insecure_channel(GRPC_ADDR) as channel:
        stub = model_deployment_pb2_grpc.ModelDeploymentServiceStub(channel)

        try:
            resp = await stub.StartDeployment(
                model_deployment_pb2.StartDeploymentRequest(
                    deployment_id=req.deployment_id
                )
            )
        except grpc.RpcError as e:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to start deployment: {e.details()}",
            )

    return {
        "deployment_id": req.deployment_id,
        "status": resp.state,
    }


@router.delete("/delete/{deployment_id}")
async def delete_deployment(deployment_id: str):
    """Permanently delete a deployment from the database.

    This should only be called on deployments that are already STOPPED or TERMINATED.
    For running deployments, use /terminate first.
    """
    import asyncpg
    from uuid import UUID

    try:
        # Validate UUID format
        dep_uuid = UUID(deployment_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid deployment ID format")

    try:
        conn = await asyncpg.connect(POSTGRES_DSN)
        try:
            # Check if deployment exists and is stopped
            row = await conn.fetchrow(
                "SELECT state FROM model_deployments WHERE deployment_id = $1", dep_uuid
            )

            if not row:
                raise HTTPException(status_code=404, detail="Deployment not found")

            # Only allow deletion of stopped/terminated/failed deployments
            if row["state"] not in ("STOPPED", "TERMINATED", "FAILED"):
                raise HTTPException(
                    status_code=400,
                    detail=f"Cannot delete deployment in state '{row['state']}'. Stop it first.",
                )

            # Clean up dependent records explicitly so deletion works even when
            # DB constraints were created without ON DELETE behaviors.
            async with conn.transaction():
                await conn.execute(
                    "UPDATE policies SET deployment_id = NULL WHERE deployment_id = $1",
                    dep_uuid,
                )
                await conn.execute(
                    "UPDATE api_keys SET deployment_id = NULL WHERE deployment_id = $1",
                    dep_uuid,
                )
                await conn.execute(
                    "DELETE FROM inference_logs WHERE deployment_id = $1",
                    dep_uuid,
                )
                await conn.execute(
                    "DELETE FROM model_deployments WHERE deployment_id = $1", dep_uuid
                )
        finally:
            await conn.close()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Failed to delete deployment: {str(e)}"
        )

    return {
        "deployment_id": deployment_id,
        "status": "DELETED",
    }


@router.post("/createpool")
async def create_pool(req: CreatePoolRequest):
    # Validate provider exists before creating pool
    try:
        adapter = get_adapter(req.provider)
        capabilities = adapter.get_capabilities()
    except ValueError as e:
        raise HTTPException(
            status_code=400, detail=f"Invalid provider '{req.provider}'. {str(e)}"
        )

    async with grpc.aio.insecure_channel(GRPC_ADDR) as channel:
        stub = compute_pool_pb2_grpc.ComputePoolManagerStub(channel)

        try:
            resp = await stub.RegisterPool(
                compute_pool_pb2.RegisterPoolRequest(
                    pool_name=req.pool_name,
                    owner_type=req.owner_type,
                    owner_id=req.owner_id,
                    provider=req.provider,
                    allowed_gpu_types=req.allowed_gpu_types,
                    max_cost_per_hour=req.max_cost_per_hour,
                    is_dedicated=req.is_dedicated,
                    provider_pool_id=req.provider_pool_id,
                    scheduling_policy_json=req.scheduling_policy_json,
                    provider_credential_name=req.provider_credential_name
                    if req.provider_credential_name
                    else "",
                )
            )

            # Log Audit Event
            # Using owner_id as user_id for now if owner_type is user, if org then context mapping needed.
            # Assuming owner_id in request context is sufficient.
            await log_audit_event(
                user_id=req.owner_id,  # This might be org_id if owner_type=org, need to be careful.
                # Ideally orchestration requests should carry user context.
                # But for now logging the request owner is best effort.
                action="pool.create",
                resource_type="compute_pool",
                resource_id=resp.pool_id,
                details={
                    "name": req.pool_name,
                    "provider": req.provider,
                    "gpu_types": req.allowed_gpu_types,
                },
            )

        except grpc.RpcError as e:
            if e.code() == grpc.StatusCode.ALREADY_EXISTS:
                raise HTTPException(status_code=409, detail=e.details())
            raise HTTPException(status_code=500, detail=e.details())

    return {
        "pool_id": resp.pool_id,
        "status": "CREATED",
    }


@router.get("/list/pool/{pool_id}/inventory")
async def list_pool_inventory(pool_id: str):
    async with grpc.aio.insecure_channel(GRPC_ADDR) as channel:
        stub = compute_pool_pb2_grpc.ComputePoolManagerStub(channel)

        try:
            resp = await stub.ListPoolInventory(
                compute_pool_pb2.ListPoolInventoryRequest(pool_id=pool_id)
            )
        except grpc.RpcError as e:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to list pool inventory: {e.details()}",
            )

    return {
        "pool_id": pool_id,
        "nodes": [
            {
                "node_id": node.node_id,
                "provider": node.provider,
                "state": node.state,
                "gpu_total": node.gpu_total,
                "gpu_allocated": node.gpu_allocated,
                "vcpu_total": node.vcpu_total,
                "vcpu_allocated": node.vcpu_allocated,
                "expose_url": node.expose_url,
            }
            for node in resp.nodes
            # if not node.state.lower().startswith("terminat") # Allowing terminated nodes if they exist for debug
        ],
    }


@router.get("/listPools/{owner_id}")
async def list_pools(owner_id: str | None = None):
    async with grpc.aio.insecure_channel(GRPC_ADDR) as channel:
        stub = compute_pool_pb2_grpc.ComputePoolManagerStub(channel)

        resp = await stub.ListPools(
            compute_pool_pb2.ListPoolsRequest(owner_id=owner_id)
        )

    return {
        "pools": [
            {
                "pool_id": p.pool_id,
                "pool_name": p.pool_name,
                "provider": p.provider,
                "is_active": p.is_active,
            }
            for p in resp.pools
        ]
    }


@router.post("/deletepool/{pool_id}")
async def delete_pool(pool_id: str):
    async with grpc.aio.insecure_channel(GRPC_ADDR) as channel:
        stub = compute_pool_pb2_grpc.ComputePoolManagerStub(channel)

        try:
            await stub.DeletePool(compute_pool_pb2.DeletePoolRequest(pool_id=pool_id))
        except grpc.RpcError as e:
            raise HTTPException(status_code=500, detail=e.details())

    return {"pool_id": pool_id, "status": "DELETED"}


@router.get("/listDeployments/{pool_id}")
async def list_deployments(pool_id: str | None = None):
    """
    List all deployments.
    Optionally filter by pool_id.
    """
    async with grpc.aio.insecure_channel(GRPC_ADDR) as channel:
        stub = model_deployment_pb2_grpc.ModelDeploymentServiceStub(channel)

        try:
            resp = await stub.ListDeployments(
                model_deployment_pb2.ListDeploymentsRequest(pool_id=pool_id or "")
            )
        except grpc.RpcError as e:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to list deployments: {e.details()}",
            )

    return {
        "deployments": [
            {
                "deployment_id": d.deployment_id,
                "model_name": d.model_name,
                "model_version": d.model_version,
                "state": d.state,
                "replicas": d.replicas,
                "pool_id": d.pool_id,
                "engine": d.engine,
                "endpoint": d.endpoint,
                "org_id": d.org_id,
            }
            for d in resp.deployments
            # if not d.state.lower().startswith("terminat") # Showing all for sticky deployment visibility
        ]
    }


@router.get("/logs/{deployment_id}")
async def get_deployment_logs(deployment_id: str):
    """
    Fetch logs for a deployment from the backend provider.
    Currently only supports Nosana (via IPFS result).
    """
    from uuid import UUID
    import asyncpg

    try:
        # 1. Get deployment to find the pool/provider
        dep_uuid = UUID(deployment_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid UUID")

    conn = await asyncpg.connect(POSTGRES_DSN)
    try:
        # Get pool_id to identify provider
        dep = await conn.fetchrow(
            """
            SELECT d.pool_id, p.provider, d.state 
            FROM model_deployments d
            JOIN compute_pools p ON d.pool_id = p.id
            WHERE d.deployment_id = $1
            """,
            dep_uuid,
        )

        if not dep:
            raise HTTPException(status_code=404, detail="Deployment/Pool not found")

        provider = dep["provider"]

        # 2. Get the Node ID / Provider Instance ID
        # Since compute_inventory lacks deployment_id, we look up via model_deployments.node_ids
        # We fetch the first node ID from the deployment's node_ids array
        dep_nodes = await conn.fetchrow(
            """
            SELECT node_ids
            FROM model_deployments
            WHERE deployment_id = $1
            """,
            dep_uuid,
        )

        if not dep_nodes or not dep_nodes["node_ids"]:
            return {"logs": ["Waiting for node provisioning..."]}

        node_id = dep_nodes["node_ids"][0]

        node = await conn.fetchrow(
            """
             SELECT provider_instance_id
             FROM compute_inventory
             WHERE id = $1
             """,
            node_id,
        )

        if not node:
            return {"logs": ["Node record not found"]}

        provider_instance_id = node["provider_instance_id"]

        # 3. Call Adapter
        try:
            adapter = get_adapter(provider)
            if hasattr(adapter, "get_logs"):
                logs_data = await adapter.get_logs(
                    provider_instance_id=provider_instance_id
                )
                return logs_data
            else:
                return {"logs": [f"Logs not supported for provider: {provider}"]}

        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Adapter error: {str(e)}")

    finally:
        await conn.close()


@router.get("/logs/{deployment_id}/stream")
async def get_deployment_log_stream_info(deployment_id: str):
    """
    Get WebSocket connection details for log streaming.
    """
    from uuid import UUID
    import asyncpg

    try:
        dep_uuid = UUID(deployment_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid UUID")

    conn = await asyncpg.connect(POSTGRES_DSN)
    try:
        # 1. Get deployment and provider
        dep = await conn.fetchrow(
            """
            SELECT p.provider, d.node_ids 
            FROM model_deployments d
            JOIN compute_pools p ON d.pool_id = p.id
            WHERE d.deployment_id = $1
            """,
            dep_uuid,
        )

        if not dep:
            raise HTTPException(status_code=404, detail="Deployment/Pool not found")

        provider = dep["provider"]
        if not dep["node_ids"]:
            return {"error": "No nodes assigned to this deployment yet."}

        node_id = dep["node_ids"][0]

        node = await conn.fetchrow(
            "SELECT provider_instance_id FROM compute_inventory WHERE id = $1", node_id
        )

        if not node:
            return {"error": "Node record not found"}

        provider_instance_id = node["provider_instance_id"]

        # 2. Call Adapter for streaming info
        try:
            adapter = get_adapter(provider)
            stream_info = await adapter.get_log_streaming_info(
                provider_instance_id=provider_instance_id
            )
            return stream_info
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Adapter error: {str(e)}")

    finally:
        await conn.close()


@router.get("/deployments")
async def list_all_deployments(org_id: str | None = None):
    """
    List ALL deployments across all pools.
    Optionally filter by org_id.
    """
    import logging

    logger = logging.getLogger("deployment-server")
    logger.info(f"list_all_deployments called for org_id: {org_id}")

    async with grpc.aio.insecure_channel(GRPC_ADDR) as channel:
        stub = model_deployment_pb2_grpc.ModelDeploymentServiceStub(channel)
        try:
            logger.info("Calling gRPC ListDeployments...")
            resp = await stub.ListDeployments(
                model_deployment_pb2.ListDeploymentsRequest(
                    pool_id="", org_id=org_id or ""
                )
            )
            logger.info(f"gRPC ListDeployments returned {len(resp.deployments)} items")
        except grpc.RpcError as e:
            logger.error(f"gRPC ListDeployments failed: {e.code()} - {e.details()}")
            raise HTTPException(
                status_code=500,
                detail=f"Failed to list all deployments: {e.details()}",
            )

    return {
        "deployments": [
            {
                "deployment_id": d.deployment_id,
                "model_name": d.model_name,
                "model_version": d.model_version + (" (Compute)" if d.pool_id else ""),
                "state": d.state,
                "replicas": d.replicas,
                "pool_id": d.pool_id,
                "created_at": None,  # or fetch if available
                "engine": d.engine,
                "endpoint": d.endpoint,
                "org_id": d.org_id,
            }
            for d in resp.deployments
            # if not d.state.lower().startswith("terminat") # Showing all for sticky deployment visibility
        ]
    }


@router.get("/provider/resources")
async def list_provider_resources(provider: str | None = None):
    """
    List available resources for a specific provider or all registered providers.

    Args:
        provider: Optional provider name. If not specified, returns resources from all providers.

    Returns:
        Dict with "resources" key containing list of available resources.
    """
    from inferia.services.orchestration.services.adapter_engine.registry import (
        ADAPTER_REGISTRY,
    )

    try:
        if provider:
            # Get resources for specific provider
            adapter = get_adapter(provider)
            resources = await adapter.discover_resources()
            return {"resources": resources, "provider": provider}
        else:
            # Get resources from all registered providers
            all_resources = []
            errors = []

            for provider_name in ADAPTER_REGISTRY.keys():
                try:
                    adapter = get_adapter(provider_name)
                    provider_resources = await adapter.discover_resources()
                    # Tag each resource with its provider
                    for resource in provider_resources:
                        resource["_provider"] = provider_name
                    all_resources.extend(provider_resources)
                except Exception as e:
                    errors.append({"provider": provider_name, "error": str(e)})

            response = {"resources": all_resources}
            if errors:
                response["errors"] = errors
            return response

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Failed to discover resources: {str(e)}"
        )


@router.post("/registerModel")
async def register_model(req: ModelRegistryRequest):
    async with grpc.aio.insecure_channel(GRPC_ADDR) as channel:
        stub = model_registry_pb2_grpc.ModelRegistryServiceStub(channel)

        try:
            resp = await stub.RegisterModel(
                model_registry_pb2.RegisterModelRequest(
                    name=req.model_name,
                    version=req.model_version,
                    backend=req.backend,
                    artifact_uri=req.artifact_uri,
                    config_json=json.dumps(req.config_json),
                )
            )
        except grpc.RpcError as e:
            raise HTTPException(
                status_code=500,
                detail=f"Model registration failed: {e.details()}",
            )

    return {
        "model_id": resp.model_id,
        "status": "REGISTERED",
    }


@router.get("/getModel/{model_name}/{model_version}")
async def get_model(model_name: str, model_version: str):
    async with grpc.aio.insecure_channel(GRPC_ADDR) as channel:
        stub = model_registry_pb2_grpc.ModelRegistryServiceStub(channel)

        try:
            resp = await stub.GetModel(
                model_registry_pb2.GetModelRequest(
                    name=model_name,
                    version=model_version,
                )
            )
        except grpc.RpcError as e:
            raise HTTPException(
                status_code=404,
                detail=f"Model not found: {e.details()}",
            )

    return {
        "model_id": resp.model_id,
        "model_name": resp.name,
        "model_version": resp.version,
        "backend": resp.backend,
        "artifact_uri": resp.artifact_uri,
        "config_json": json.loads(resp.config_json),
    }


@router.delete("/deleteModel")
async def delete_model(req: DeleteModelRequest):
    async with grpc.aio.insecure_channel(GRPC_ADDR) as channel:
        stub = model_registry_pb2_grpc.ModelRegistryServiceStub(channel)

        try:
            await stub.DeleteModel(
                model_registry_pb2.DeleteModelRequest(model_id=req.model_id)
            )
        except grpc.RpcError as e:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to delete model: {e.details()}",
            )

    return {
        "model_id": req.model_id,
        "status": "DELETED",
    }


@router.get("/listModels/{model_name}")
async def list_models(model_name: str | None = None):
    async with grpc.aio.insecure_channel(GRPC_ADDR) as channel:
        stub = model_registry_pb2_grpc.ModelRegistryServiceStub(channel)

        try:
            resp = await stub.ListModels(
                model_registry_pb2.ListModelsRequest(name=model_name)
            )
        except grpc.RpcError as e:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to list models: {e.details()}",
            )

    return {
        "models": [
            {
                "model_id": m.model_id,
                "model_name": m.name,
                "model_version": m.version,
                "backend": m.backend,
                "artifact_uri": m.artifact_uri,
                "config_json": json.loads(m.config_json),
            }
            for m in resp.models
        ]
    }

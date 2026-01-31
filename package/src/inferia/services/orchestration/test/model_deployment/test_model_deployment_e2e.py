import asyncio
import grpc
import json
from uuid import UUID

from infra.db import create_test_db_pool
from infra.test_setup import uid

from v1 import (
    model_registry_pb2,
    model_registry_pb2_grpc,
    model_deployment_pb2,
    model_deployment_pb2_grpc,
)


async def test_model_deployment_e2e():
    print("\n=== MODEL DEPLOYMENT E2E TEST ===\n")

    db = await create_test_db_pool()
    channel = grpc.aio.insecure_channel("localhost:50051")

    deployer = model_deployment_pb2_grpc.ModelDeploymentServiceStub(channel)


    # -------------------------------------------------
    # 2. DEPLOY MODEL
    # -------------------------------------------------
    print("\n2) Deploying model")

    pool_id = await db.fetchval(
        """
        SELECT id FROM compute_pools
        WHERE is_active = true
        ORDER BY created_at DESC
        LIMIT 1
        """
    )

    assert pool_id, "No compute pool available for deployment"

    deploy = await deployer.DeployModel(
        model_deployment_pb2.DeployModelRequest(
            model_name="llama-3-14b",
            model_version="v1",
            pool_id=str(pool_id),
            replicas=1,
            gpu_per_replica=0,
            workload_type="inference",
        )
    )

    deployment_id = deploy.deployment_id
    print(f"   ✔ Deployment started: {deployment_id}")
    assert deploy.state == "provisioning"

    # -------------------------------------------------
    # 3. WAIT FOR DEPLOYMENT STATE
    # -------------------------------------------------
    print("\n3) Waiting for deployment to progress")

    state = None
    for _ in range(30):
        d = await deployer.GetDeployment(
            model_deployment_pb2.GetDeploymentRequest(
                deployment_id=deployment_id
            )
        )
        state = d.state
        print(f"   • current state: {state}")
        if state in ("ready", "failed"):
            break
        await asyncio.sleep(1)

    assert state == "ready", f"Deployment ended in state={state}"

    # -------------------------------------------------
    # 4. VERIFY llm-d RESOURCE
    # -------------------------------------------------
    print("\n4) Verifying llm-d resource")

    row = await db.fetchrow(
        """
        SELECT llmd_resource_name
        FROM model_deployments
        WHERE deployment_id=$1
        """,
        UUID(deployment_id),
    )

    assert row and row["llmd_resource_name"]
    print(f"   ✔ llm-d resource created: {row['llmd_resource_name']}")

    print("\n=== MODEL DEPLOYMENT TEST PASSED ===\n")


if __name__ == "__main__":
    asyncio.run(test_model_deployment_e2e())

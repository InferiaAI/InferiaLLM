import asyncio
import grpc
import time
import uuid

from inferia.services.orchestration.v1 import (
    model_registry_pb2,
    model_registry_pb2_grpc,
    model_deployment_pb2,
    model_deployment_pb2_grpc,
    compute_pool_pb2,
    compute_pool_pb2_grpc,
)

GRPC_ADDR = "localhost:50051"


async def run_test():
    async with grpc.aio.insecure_channel(GRPC_ADDR) as channel:

        # ============================================================
        # 1. CREATE COMPUTE POOL (AWS + A100)
        # ============================================================
        pool_mgr = compute_pool_pb2_grpc.ComputePoolManagerStub(channel)
        pool_name = uuid.uuid4()
        model_name = f"llama3-8b-{str(uuid.uuid4())[:8]}"

        print("\n==> Creating A100 compute pool")

        pool_resp = await pool_mgr.RegisterPool(
            compute_pool_pb2.RegisterPoolRequest(
                pool_name=str(pool_name),
                owner_type="system",
                owner_id="system",
                provider="nosana",
                allowed_gpu_types=["A100"],
                max_cost_per_hour=10.0,
                is_dedicated=True,
                scheduling_policy_json="""
                {
                    "strategy": "best_fit",
                    "allow_provisioning": true
                }
                """,
            )
        )

        pool_id = pool_resp.pool_id
        print(f"✔ Pool created: {pool_id}")

        # ============================================================
        # 2. REGISTER MODEL
        # ============================================================
        registry = model_registry_pb2_grpc.ModelRegistryStub(channel)

        print("\n==> Registering model")

        model_resp = await registry.RegisterModel(
            model_registry_pb2.RegisterModelRequest(
                name=model_name,
                version="v1",
                backend="vllm",
                artifact_uri="meta-llama/Meta-Llama-3-8B",
                config_json="""
                {
                    "task": "text-generation",
                    "framework": "pytorch"
                }
                """,
            )
        )

        print(f"✔ Model registered: {model_resp.model_id}")

        # ============================================================
        # 3. DEPLOY MODEL (ONLY PUBLIC CALL)
        # ============================================================
        deployer = model_deployment_pb2_grpc.ModelDeploymentServiceStub(channel)

        print("\n==> Deploying model (AWS A100 via SkyPilot)")

        deploy_resp = await deployer.DeployModel(
            model_deployment_pb2.DeployModelRequest(
                model_name=model_name,
                model_version="v1",
                pool_id=pool_id,
                replicas=1,
                gpu_per_replica=1,
                workload_type="inference",
            )
        )

        deployment_id = deploy_resp.deployment_id
        print(f"✔ Deployment accepted: {deployment_id}")

        # ============================================================
        # 4. POLL DEPLOYMENT STATE
        # ============================================================
        print("\n==> Waiting for deployment to become RUNNING")
        print("   (this includes provisioning, scheduling, runtime launch)")

        while True:
            resp = await deployer.GetDeployment(
                model_deployment_pb2.GetDeploymentRequest(
                    deployment_id=deployment_id
                )
            )

            print(f"   state = {resp.state}")

            if resp.state == "RUNNING":
                break
            if resp.state == "FAILED":
                raise RuntimeError("Deployment failed")

            await asyncio.sleep(10)

        print("\n✔ Deployment is RUNNING")

        # ============================================================
        # 5. VERIFY DEPLOYMENT LIST
        # ============================================================
        print("\n==> Listing deployments")

        list_resp = await deployer.ListDeployments(
            model_deployment_pb2.ListDeploymentsRequest(
                pool_id=pool_id
            )
        )

        for d in list_resp.deployments:
            print(
                f"- deployment_id={d.deployment_id} "
                f"state={d.state} replicas={d.replicas}"
            )

        print("\n✅ AWS A100 SKY PILOT DEPLOYMENT TEST PASSED")


if __name__ == "__main__":
    asyncio.run(run_test())

"""
End-to-end test: GCP GPU provisioning -> vLLM deployment -> teardown.

Prerequisites:
  - Orchestration gRPC server running on localhost:50051
  - GCP credentials configured (`gcloud auth application-default login`)
  - `sky check` shows GCP enabled
  - `pip install "skypilot[gcp]"`

Usage:
  python -m inferia.services.orchestration.test.adapter_test.test_gcp_vllm_deploy
"""

import asyncio
import uuid
import time

import grpc

from inferia.services.orchestration.v1 import (
    compute_pool_pb2,
    compute_pool_pb2_grpc,
    model_registry_pb2,
    model_registry_pb2_grpc,
    model_deployment_pb2,
    model_deployment_pb2_grpc,
)

GRPC_ADDR = "localhost:50051"

# Adjust these to match your GCP quota and model requirements
GPU_TYPE = "L4"
MODEL_URI = "meta-llama/Meta-Llama-3-8B-Instruct"
MODEL_NAME = f"llama3-8b-gcp-{str(uuid.uuid4())[:8]}"
POOL_NAME = f"gcp-vllm-test-{str(uuid.uuid4())[:8]}"


async def run_test():
    start_time = time.time()

    async with grpc.aio.insecure_channel(GRPC_ADDR) as channel:

        # ============================================================
        # 1. CREATE COMPUTE POOL (GCP)
        # ============================================================
        pool_mgr = compute_pool_pb2_grpc.ComputePoolManagerStub(channel)

        print(f"\n==> Creating GCP compute pool ({GPU_TYPE})")

        pool_resp = await pool_mgr.RegisterPool(
            compute_pool_pb2.RegisterPoolRequest(
                pool_name=POOL_NAME,
                owner_type="system",
                owner_id="system",
                provider="gcp",
                allowed_gpu_types=[GPU_TYPE],
                max_cost_per_hour=10.0,
                is_dedicated=True,
                scheduling_policy_json="""{
                    "strategy": "best_fit",
                    "allow_provisioning": true
                }""",
            )
        )

        pool_id = pool_resp.pool_id
        print(f"   Pool created: {pool_id}")

        # ============================================================
        # 2. REGISTER MODEL (vLLM backend)
        # ============================================================
        registry = model_registry_pb2_grpc.ModelRegistryStub(channel)

        print(f"\n==> Registering model: {MODEL_NAME} (vLLM)")

        model_resp = await registry.RegisterModel(
            model_registry_pb2.RegisterModelRequest(
                name=MODEL_NAME,
                version="v1",
                backend="vllm",
                artifact_uri=MODEL_URI,
                config_json="""{
                    "task": "text-generation",
                    "framework": "pytorch",
                    "engine_args": {
                        "max-model-len": 4096,
                        "dtype": "auto",
                        "gpu-memory-utilization": 0.90
                    }
                }""",
            )
        )

        print(f"   Model registered: {model_resp.model_id}")

        # ============================================================
        # 3. DEPLOY MODEL ON GCP
        # ============================================================
        deployer = model_deployment_pb2_grpc.ModelDeploymentServiceStub(channel)

        print(f"\n==> Deploying {MODEL_NAME} on GCP {GPU_TYPE} via SkyPilot")

        deploy_resp = await deployer.DeployModel(
            model_deployment_pb2.DeployModelRequest(
                model_name=MODEL_NAME,
                model_version="v1",
                pool_id=pool_id,
                replicas=1,
                gpu_per_replica=1,
                workload_type="inference",
            )
        )

        deployment_id = deploy_resp.deployment_id
        print(f"   Deployment accepted: {deployment_id}")
        print(f"   Initial state: {deploy_resp.state}")

        # ============================================================
        # 4. POLL UNTIL RUNNING
        # ============================================================
        print("\n==> Waiting for deployment to become RUNNING")
        print("    (provisioning GCP VM + launching vLLM — may take a few minutes)")

        timeout = 600  # 10 min max
        poll_start = time.time()

        while True:
            resp = await deployer.GetDeployment(
                model_deployment_pb2.GetDeploymentRequest(
                    deployment_id=deployment_id
                )
            )

            elapsed = int(time.time() - poll_start)
            print(f"   [{elapsed:>3d}s] state = {resp.state}")

            if resp.state == "RUNNING":
                break
            if resp.state == "FAILED":
                print("\n   DEPLOYMENT FAILED")
                # Still attempt cleanup
                break

            if time.time() - poll_start > timeout:
                print("\n   TIMEOUT waiting for deployment")
                break

            await asyncio.sleep(15)

        # ============================================================
        # 5. VERIFY DEPLOYMENT IN LIST
        # ============================================================
        print("\n==> Listing deployments in pool")

        list_resp = await deployer.ListDeployments(
            model_deployment_pb2.ListDeploymentsRequest(pool_id=pool_id)
        )

        for d in list_resp.deployments:
            print(
                f"   - deployment_id={d.deployment_id} "
                f"state={d.state} replicas={d.replicas}"
            )

        # ============================================================
        # 6. DELETE DEPLOYMENT (teardown vLLM + deprovision GPU)
        # ============================================================
        print(f"\n==> Deleting deployment {deployment_id}")

        delete_resp = await deployer.DeleteDeployment(
            model_deployment_pb2.DeleteDeploymentRequest(
                deployment_id=deployment_id
            )
        )

        print(f"   Delete accepted: {delete_resp.accepted}")

        # Poll until deployment is gone or terminated
        print("   Waiting for teardown...")

        for _ in range(30):
            try:
                resp = await deployer.GetDeployment(
                    model_deployment_pb2.GetDeploymentRequest(
                        deployment_id=deployment_id
                    )
                )
                if resp.state in ("TERMINATED", "DELETED", "STOPPED"):
                    print(f"   Deployment state: {resp.state}")
                    break
                print(f"   Teardown in progress... state={resp.state}")
            except grpc.aio.AioRpcError as e:
                if e.code() == grpc.StatusCode.NOT_FOUND:
                    print("   Deployment removed")
                    break
                raise

            await asyncio.sleep(10)

        # ============================================================
        # DONE
        # ============================================================
        total = int(time.time() - start_time)
        print(f"\n   GCP vLLM DEPLOYMENT TEST COMPLETE ({total}s total)")


if __name__ == "__main__":
    asyncio.run(run_test())

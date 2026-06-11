import asyncio
import grpc
from uuid import UUID

from inferia.services.orchestration.v1 import (
    model_registry_pb2,
    model_registry_pb2_grpc,
    model_deployment_pb2,
    model_deployment_pb2_grpc,
)


GRPC_ADDR = "localhost:50051"

# CHANGE THIS to an existing pool_id in your DB
POOL_ID = "0ff001ed-aed0-4be0-afb0-f90b53d5cae4"


async def run_test():
    async with grpc.aio.insecure_channel(GRPC_ADDR) as channel:

        # -------------------------------
        # 1. Register Model
        # -------------------------------
        registry = model_registry_pb2_grpc.ModelRegistryStub(channel)

        print("\n==> Registering model")

        register_resp = await registry.RegisterModel(
            model_registry_pb2.RegisterModelRequest(
                name="llama-3-test",
                version="v1",
                backend="llmd",
                artifact_uri="/models/llama3",  # must exist on node
                # framework="pytorch",
                # task="text-generation",
                config_json='{"model_size":"7B", "framework":"pytorch", "task":"text-generation"}',
            )
        )

        print("✔ Model registered")
        print("  model_id:", register_resp.model_id)

        # -------------------------------
        # 2. Deploy Model
        # -------------------------------
        deployer = model_deployment_pb2_grpc.ModelDeploymentServiceStub(
            channel
        )

        print("\n==> Deploying model")

        deploy_resp = await deployer.DeployModel(
            model_deployment_pb2.DeployModelRequest(
                model_name="llama-3-test",
                model_version="v1",
                pool_id=POOL_ID,
                replicas=1,
                gpu_per_replica=1,
                workload_type="inference",
            )
        )

        deployment_id = deploy_resp.deployment_id

        print("✔ Deployment accepted")
        print("  deployment_id:", deployment_id)
        print("  state:", deploy_resp.state)

        # -------------------------------
        # 3. Poll Deployment State
        # -------------------------------
        print("\n==> Waiting for deployment to become RUNNING")

        while True:
            resp = await deployer.GetDeployment(
                model_deployment_pb2.GetDeploymentRequest(
                    deployment_id=deployment_id
                )
            )

            print("  state =", resp.state)

            if resp.state in ("RUNNING", "FAILED"):
                break

            await asyncio.sleep(5)

        if resp.state == "FAILED":
            raise RuntimeError("Deployment failed")

        print("\n✔ Deployment is RUNNING")

        # -------------------------------
        # 4. List Deployments
        # -------------------------------
        print("\n==> Listing deployments")

        list_resp = await deployer.ListDeployments(
            model_deployment_pb2.ListDeploymentsRequest(
                pool_id=POOL_ID
            )
        )

        for d in list_resp.deployments:
            print(
                f"- deployment_id={d.deployment_id}, "
                f"state={d.state}, "
                f"replicas={d.replicas}"
            )

        print("\n✅ gRPC MODEL DEPLOYMENT TEST PASSED")


if __name__ == "__main__":
    asyncio.run(run_test())

import boto3
import grpc
import asyncio
import functools
import datetime as dt
import os
from adapters.base.adapter import ProviderAdapter
from orchestration.v1 import (
    compute_node_pb2_grpc,
    compute_node_pb2,
)

# Same env knob as services/model_deployment/deployment_server.py — must follow
# GRPC_PORT when the orchestration gRPC server is remapped, or this dials
# whatever else holds 50051.
GRPC_ADDR = os.getenv("ORCHESTRATION_GRPC_ADDR", "localhost:50051")


async def _run_sync(func, *args, **kwargs):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, functools.partial(func, *args, **kwargs))

AWS_INSTANCE_RESOURCES = {
    "g4dn.xlarge": {"cpu": "4", "memory": "16Gi"},
    "g4dn.2xlarge": {"cpu": "8", "memory": "32Gi"},
    "g4dn.4xlarge": {"cpu": "16", "memory": "64Gi"},
    "g4dn.8xlarge": {"cpu": "32", "memory": "128Gi"},
    "g4dn.12xlarge": {"cpu": "48", "memory": "192Gi"},
    "g4dn.16xlarge": {"cpu": "64", "memory": "256Gi"},
    "g5.xlarge": {"cpu": "4", "memory": "16Gi"},
    "g5.2xlarge": {"cpu": "8", "memory": "32Gi"},
    "g5.4xlarge": {"cpu": "16", "memory": "64Gi"},
    "g5.8xlarge": {"cpu": "32", "memory": "64Gi"},
    "g5.12xlarge": {"cpu": "48", "memory": "192Gi"},
    "g5.16xlarge": {"cpu": "64", "memory": "256Gi"},
    "g5.48xlarge": {"cpu": "192", "memory": "768Gi"},
    "p3.2xlarge": {"cpu": "8", "memory": "61Gi"},
    "p3.8xlarge": {"cpu": "32", "memory": "244Gi"},
    "p3.16xlarge": {"cpu": "64", "memory": "488Gi"},
    "p4d.24xlarge": {"cpu": "96", "memory": "1152Gi"},
    "p5.48xlarge": {"cpu": "192", "memory": "2048Gi"},
}


class AWSAdapter(ProviderAdapter):
    def __init__(self, region: str):
        self.ec2 = boto3.client("ec2", region_name=region)

    async def discover_nodes(self):
        resp = await _run_sync(
            self.ec2.describe_instances,
            Filters=[
                {"Name": "tag:inferia-managed", "Values": ["true"]},
                {"Name": "instance-state-name", "Values": ["running"]},
            ],
        )

        nodes = []
        for r in resp["Reservations"]:
            for i in r["Instances"]:
                nodes.append({
                    "provider": "aws",
                    "node_id": i["InstanceId"],
                    "instance_type": i["InstanceType"],
                    "region": self.ec2.meta.region_name,
                    "private_ip": i.get("PrivateIpAddress"),
                })
        return nodes

    async def get_node_metadata(self, node_id: str):
        try:
            resp = await _run_sync(
                self.ec2.describe_instances,
                InstanceIds=[node_id],
            )
            reservations = resp.get("Reservations", [])
            if not reservations:
                return {}
            instances = reservations[0].get("Instances", [])
            if not instances:
                return {}
            instance = instances[0]
            metadata = {
                "region": self.ec2.meta.region_name,
                "instance_type": instance.get("InstanceType"),
                "availability_zone": instance.get("Placement", {}).get("AvailabilityZone"),
            }
            return metadata
        except Exception:
            pass

        return {}

    async def reconcile(self):
        nodes = await self.discover_nodes()

        async with grpc.aio.insecure_channel(GRPC_ADDR) as channel:
            stub = compute_node_pb2_grpc.ComputeNodeServiceStub(channel)

            for node in nodes:
                try:
                    await stub.RegisterNode(
                        compute_node_pb2.RegisterNodeRequest(
                            pool_id="aws-default-pool",
                            node_name=node["node_id"],
                            node_type=node["instance_type"],
                            allocatable=AWS_INSTANCE_RESOURCES.get(
                                node["instance_type"],
                                {"cpu": "4", "memory": "16Gi"},
                            ),
                        )
                    )
                except grpc.aio.AioRpcError as e:
                    if e.code() == grpc.StatusCode.ALREADY_EXISTS:
                        continue
                    else:
                        raise e

        return nodes 
    

        

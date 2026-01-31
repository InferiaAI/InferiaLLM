import boto3
import grpc
import asyncio
import datetime as dt
from adapters.base.adapter import ProviderAdapter
from app.v1 import (
    compute_node_pb2_grpc,
    compute_node_pb2,
)

GRPC_ADDR = "localhost:50051"


class AWSAdapter(ProviderAdapter):
    def __init__(self, region: str):
        self.ec2 = boto3.client("ec2", region_name=region)

    async def discover_nodes(self):
        resp = self.ec2.describe_instances(
            Filters=[
                {"Name": "tag:inferia-managed", "Values": ["true"]},
                {"Name": "instance-state-name", "Values": ["running"]},
            ]
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
            resp = self.ec2.describe_instances(
                InstanceIds=[node_id]
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
                            allocatable={
                                "cpu": "4",  # Placeholder
                                "memory": "16Gi",  # Placeholder
                            },
                        )
                    )
                except grpc.aio.AioRpcError as e:
                    if e.code() == grpc.StatusCode.ALREADY_EXISTS:
                        continue
                    else:
                        raise e

        return nodes 
    

        

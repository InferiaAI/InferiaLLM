import boto3
from collections import defaultdict
from tabulate import tabulate

from aws_instance_specs import INSTANCE_SPECS
from dotenv import load_dotenv

load_dotenv()

REGION = "us-east-1"
TAG_KEY = "inferia-managed"
TAG_VALUE = "true"


def discover_aws_nodes():
    ec2 = boto3.client("ec2", region_name=REGION)

    resp = ec2.describe_instances(
        Filters=[
            {"Name": f"tag:{TAG_KEY}", "Values": [TAG_VALUE]},
            {"Name": "instance-state-name", "Values": ["running"]},
        ]
    )

    nodes = []

    for reservation in resp["Reservations"]:
        for inst in reservation["Instances"]:
            itype = inst["InstanceType"]
            spec = INSTANCE_SPECS.get(itype)

            if not spec:
                continue  # skip unsupported types

            nodes.append({
                "node_id": inst["InstanceId"],
                "instance_type": itype,
                "az": inst["Placement"]["AvailabilityZone"],
                "cpu": spec["cpu"],
                "memory_gb": spec["memory_gb"],
                "gpu": spec["gpu"],
                "gpu_type": spec["gpu_type"],
            })

    return nodes


def group_into_pools(nodes):
    pools = defaultdict(list)

    for n in nodes:
        pool_key = f"{REGION}-{n['gpu_type']}"
        pools[pool_key].append(n)

    return pools


def print_report(pools):
    print("\n=== AWS REAL-WORLD AVAILABLE CAPACITY ===\n")

    for pool, nodes in pools.items():
        total_cpu = sum(n["cpu"] for n in nodes)
        total_mem = sum(n["memory_gb"] for n in nodes)
        total_gpu = sum(n["gpu"] for n in nodes)

        print(f"\nPOOL: {pool}")
        print(f"Nodes: {len(nodes)}")
        print(f"Total CPU: {total_cpu}")
        print(f"Total Memory (GB): {total_mem}")
        print(f"Total GPU: {total_gpu}")

        table = [
            [
                n["node_id"],
                n["instance_type"],
                n["cpu"],
                n["memory_gb"],
                n["gpu"],
                n["gpu_type"],
                n["az"],
            ]
            for n in nodes
        ]

        print(
            tabulate(
                table,
                headers=[
                    "Node ID",
                    "Instance Type",
                    "CPU",
                    "Memory(GB)",
                    "GPU",
                    "GPU Type",
                    "AZ",
                ],
                tablefmt="grid",
            )
        )


if __name__ == "__main__":
    nodes = discover_aws_nodes()
    pools = group_into_pools(nodes)
    print_report(pools)

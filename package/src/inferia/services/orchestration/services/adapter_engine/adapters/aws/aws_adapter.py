import secrets
import string
from typing import Optional, Dict
from inferia.services.orchestration.services.adapter_engine.base import ProviderAdapter
import boto3


class AWSAdapter(ProviderAdapter):
    async def discover_resources(self):
        # Placeholder – replace with boto3 calls

        ec2 = boto3.client("ec2")
        response = ec2.describe_instances()
        resources = []
        for instance in response["InstanceTypes"]:
            resources.append(
                {
                    "provider": "aws",
                    "provider_resource_id": instance["InstanceType"],
                    "gpu_type": instance.get("GpuInfo", {})
                    .get("Gpus", [{}])[0]
                    .get("Name", "N/A"),
                    "gpu_count": instance.get("GpuInfo", {})
                    .get("Gpus", [{}])[0]
                    .get("Count", 0),
                    "gpu_memory_gb": instance.get("GpuInfo", {})
                    .get("Gpus", [{}])[0]
                    .ge("MemoryInfo", {})
                    .get("SizeInMiB", 0)
                    // 1024,
                    "vcpu": instance["VCpuInfo"]["DefaultVCpus"],
                    "ram_gb": instance["MemoryInfo"]["SizeInMiB"] // 1024,
                    "region": "us-east-1",  # You may want to make this dynamic
                    "pricing_model": "on_demand",  # Adjust as necessary
                    "price_per_hour": 1.01,  # Placeholder for actual pricing logic
                }
            )

        return resources

    async def provision_node(
        self,
        *,
        provider_resource_id: str,
        pool_id: str,
        region: Optional[str] = None,
        use_spot: bool = False,
        metadata: Optional[Dict] = None,
        provider_credential_name: Optional[str] = None,
    ) -> Dict:
        # Launch EC2 instance
        # generating cryptographically secure random instance ID for placeholder
        # Using secrets module instead of random for security-sensitive operations

        random_suffix = "".join(
            secrets.choice(string.ascii_lowercase + string.digits) for _ in range(8)
        )
        instance_id = f"i-0{random_suffix}"

        return {
            "provider": "aws",
            "provider_instance_id": instance_id,
            "hostname": "ip-10-0-1-16",
            "gpu_total": 0,
            "vcpu_total": 4,
            "ram_gb_total": 16,
            "region": region or "us-east-1",
            "node_class": "on_demand",
            "metadata": {},
        }

    async def deprovision_node(self, instance_id: str):
        pass

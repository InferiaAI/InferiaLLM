from uuid import UUID
from inferia.services.orchestration.v1 import (
    adapter_engine_pb2,
    adapter_engine_pb2_grpc,
)
from inferia.services.orchestration.services.adapter_engine.registry import get_adapter


class AdapterEngineService(adapter_engine_pb2_grpc.AdapterEngineServicer):
    def __init__(self, provider_repo, node_repo, inventory_repo):
        self.provider_repo = provider_repo
        self.node_repo = node_repo
        self.inventory_repo = inventory_repo

    async def DiscoverProviderResources(self, request, context):
        adapter = get_adapter(request.provider)
        resources = await adapter.discover_resources()

        for r in resources:
            await self.provider_repo.upsert_provider_resource(r)

        return adapter_engine_pb2.EmptyMessage()

    async def ProvisionNode(self, request, context):
        adapter = get_adapter(request.provider)

        node = await adapter.provision_node(
            provider_resource_id=request.provider_resource_id,
            pool_id=request.pool_id,
            region=request.region,
            use_spot=request.use_spot,
            metadata=dict(request.metadata),
            provider_credential_name=request.provider_credential_name
            if request.provider_credential_name
            else None,
        )

        node_id = await self.inventory_repo.register_node(
            pool_id=UUID(request.pool_id),
            provider=node["provider"],
            provider_instance_id=node["provider_instance_id"],
            provider_resource_id=None,
            hostname=node["provider_instance_id"],
            gpu_total=node["gpu_total"],
            vcpu_total=node["vcpu_total"],
            ram_gb_total=node["ram_gb_total"],
            state="provisioning",
            node_class=node["node_class"],
            metadata=node["metadata"],
        )

        return adapter_engine_pb2.ProvisionNodeResponse(node_id=str(node_id))

    async def DeprovisionNode(self, request, context):
        node = await self.node_repo.get_node_by_id(UUID(request.node_id))
        if not node:
            raise Exception(f"Node {request.node_id} not found")

        adapter = get_adapter(node["provider"])

        await adapter.deprovision_node(
            provider_instance_id=node["provider_instance_id"]
        )

        await self.inventory_repo.update_node_state(
            UUID(request.node_id), "deprovisioning"
        )

        return adapter_engine_pb2.EmptyMessage()

from services.adapter_engine.registry import get_adapter
from services.adapter_engine.types import NodeProvisionSpec


class AdapterEngine:
    def __init__(self, inventory_repo):
        self.inventory = inventory_repo

    # -------------------------------------------------
    # PROVISION (PHASE 1: INTENT ONLY)
    # -------------------------------------------------
    async def provision_node(
        self,
        *,
        provider: str,
        provider_resource_id: str,
        pool_id,
        region: str | None = None,
        use_spot: bool = False,
    ):
        adapter = get_adapter(provider)

        # ---- Call provider ----
        spec: NodeProvisionSpec = await adapter.provision_node(
            provider_resource_id=provider_resource_id,
            region=region,
            use_spot=use_spot,
        )

        # ---- Register as PROVISIONING ----
        node_id = await self.inventory.register_node(
            pool_id=pool_id,
            provider=spec.provider,
            provider_instance_id=spec.provider_instance_id,
            provider_resource_id=spec.instance_type,
            hostname=None,                     # ← intentionally NULL
            gpu_total=spec.gpu_total,
            vcpu_total=spec.vcpu_total,
            ram_gb_total=spec.ram_gb_total,
            state="provisioning",
            node_class=spec.node_class,
            metadata=spec.metadata,
        )

        return node_id

    # -------------------------------------------------
    # DEPROVISION (SAFE + RETRYABLE)
    # -------------------------------------------------
    async def deprovision_node(self, *, node_id):
        node = await self.inventory.get(node_id)
        if not node:
            return

        if node["state"] == "terminated":
            return

        # ---- mark intent ----
        await self.inventory.update_state(node_id, "deleting")

        adapter = get_adapter(node["provider"])

        try:
            await adapter.deprovision_node(
                provider_instance_id=node["provider_instance_id"]
            )
        except Exception:
            # DO NOT DELETE INVENTORY
            # Reaper will retry
            raise

        # ---- final cleanup ----
        await self.inventory.delete(node_id)

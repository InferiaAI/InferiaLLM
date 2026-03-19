class FakeAdapterStub:
    """
    In-memory adapter stub for testing autoscaler and other components
    that call provision_node / deprovision_node.
    """

    def __init__(self):
        self.provision_calls = []
        self.deprovision_calls = []

    async def provision_node(self, *, provider, provider_resource_id, pool_id, **kwargs):
        self.provision_calls.append({
            "provider": provider,
            "provider_resource_id": provider_resource_id,
            "pool_id": pool_id,
            **kwargs,
        })
        return {
            "provider": provider,
            "provider_instance_id": f"fake-{pool_id}",
        }

    async def deprovision_node(self, *, provider, provider_instance_id, **kwargs):
        self.deprovision_calls.append({
            "provider": provider,
            "provider_instance_id": provider_instance_id,
            **kwargs,
        })

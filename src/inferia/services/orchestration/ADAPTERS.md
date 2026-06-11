# Compute Abstraction Layer - Adapter System

## Overview

The Compute Abstraction Layer provides a unified interface for provisioning compute resources across different providers including DePIN networks (Nosana, Akash), cloud providers (AWS via SkyPilot), and on-premises infrastructure (Kubernetes).

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    Adapter Engine                            │
├─────────────────────────────────────────────────────────────┤
│  Registry (Factory)                                          │
│  ├── get_adapter("nosana") → NosanaAdapter                  │
│  ├── get_adapter("akash") → AkashAdapter                    │
│  ├── get_adapter("k8s") → KubernetesAdapter                 │
│  └── get_adapter("aws") → SkyPilotAdapter                   │
├─────────────────────────────────────────────────────────────┤
│  Base Interface (ProviderAdapter)                            │
│  ├── discover_resources()                                    │
│  ├── provision_node()                                        │
│  ├── wait_for_ready()                                        │
│  ├── deprovision_node()                                      │
│  ├── get_logs()                                              │
│  └── get_log_streaming_info()                               │
├─────────────────────────────────────────────────────────────┤
│  ProviderCapabilities                                        │
│  ├── is_ephemeral: bool                                      │
│  ├── readiness_timeout_seconds: int                          │
│  ├── supports_log_streaming: bool                            │
│  └── ...                                                     │
└─────────────────────────────────────────────────────────────┘
```

## Provider Types

### DePIN Networks
- **Nosana**: Job-based GPU marketplace with fixed pricing
- **Akash**: SDL-based auction marketplace with dynamic pricing

### Cloud Providers
- **AWS**: Via SkyPilot multi-cloud adapter
- **GCP/Azure**: Supported through SkyPilot

### On-Premises
- **Kubernetes**: Native K8s pod-based provisioning

## ProviderCapabilities System

Each adapter exposes capabilities that inform the orchestration layer how to handle the provider:

```python
@dataclass
class ProviderCapabilities:
    # Core features
    supports_log_streaming: bool = False
    supports_confidential_compute: bool = False
    supports_spot_instances: bool = False
    supports_multi_gpu: bool = True
    
    # Lifecycle
    is_ephemeral: bool = False
    requires_readiness_poll: bool = True
    readiness_timeout_seconds: int = 300
    polling_interval_seconds: int = 20
    
    # Integration
    requires_sidecar: bool = False
    supports_direct_provisioning: bool = True
    
    # Pricing
    pricing_model: PricingModel = PricingModel.FIXED
```

### Capability Descriptions

- **is_ephemeral**: Provider manages node lifecycle externally (DePIN jobs, spot instances). These nodes should be terminated rather than recycled.
- **requires_readiness_poll**: Whether the adapter needs to poll for node readiness or if provisioning is synchronous.
- **readiness_timeout_seconds**: How long to wait for a node to become ready.
- **supports_log_streaming**: Whether real-time log streaming via WebSocket is available.
- **pricing_model**: FIXED, SPOT, AUCTION, or ON_DEMAND.

## Creating a New Adapter

### 1. Implement the Base Class

```python
from inferia.services.orchestration.services.adapter_engine.base import (
    ProviderAdapter,
    AdapterType,
    PricingModel,
    ProviderCapabilities,
)

class MyProviderAdapter(ProviderAdapter):
    ADAPTER_TYPE = AdapterType.CLOUD  # or DEPIN, ON_PREM
    
    CAPABILITIES = ProviderCapabilities(
        supports_log_streaming=True,
        is_ephemeral=False,
        readiness_timeout_seconds=120,
        pricing_model=PricingModel.ON_DEMAND,
    )
    
    async def discover_resources(self) -> List[Dict]:
        # Return list of available resources
        return [{
            "provider": "myprovider",
            "provider_resource_id": "instance-type-id",
            "gpu_type": "nvidia-a100",
            "gpu_count": 8,
            "vcpu": 96,
            "ram_gb": 384,
            "region": "us-east-1",
            "pricing_model": self.CAPABILITIES.pricing_model.value,
            "price_per_hour": 3.50,
        }]
    
    async def provision_node(self, *, provider_resource_id: str, 
                            pool_id: str, region: Optional[str] = None,
                            use_spot: bool = False, 
                            metadata: Optional[Dict] = None) -> Dict:
        # Provision and return node specification
        return {
            "provider": "myprovider",
            "provider_instance_id": "instance-123",
            "hostname": "myprovider-instance-123",
            "gpu_total": 8,
            "vcpu_total": 96,
            "ram_gb_total": 384,
            "region": region or "us-east-1",
            "node_class": "on_demand",
            "metadata": {},
        }
    
    async def wait_for_ready(self, *, provider_instance_id: str, 
                            timeout: int = 300) -> str:
        # Poll until ready, return endpoint URL
        return "https://instance-123.myprovider.com"
    
    async def deprovision_node(self, *, provider_instance_id: str) -> None:
        # Clean up the instance
        pass
    
    async def get_logs(self, *, provider_instance_id: str) -> Dict:
        # Fetch logs
        return {"logs": ["log line 1", "log line 2"]}
    
    async def get_log_streaming_info(self, *, provider_instance_id: str) -> Dict:
        # Return WebSocket connection info
        return {
            "ws_url": "wss://logs.myprovider.com/stream",
            "provider": "myprovider",
            "subscription": {"instance_id": provider_instance_id},
        }
```

### 2. Register the Adapter

Add to `registry.py`:

```python
from inferia.services.orchestration.services.adapter_engine.adapters.myprovider.myprovider_adapter import MyProviderAdapter

ADAPTER_REGISTRY = {
    # ... existing providers
    "myprovider": MyProviderAdapter,
}
```

### 3. Add Configuration

Update `config.py`:

```python
class Settings(BaseSettings):
    # ... existing settings
    
    myprovider_api_key: str = Field(
        default="",
        validation_alias="MYPROVIDER_API_KEY"
    )
    myprovider_region: str = Field(
        default="us-east-1",
        validation_alias="MYPROVIDER_REGION"
    )
```

### 4. Add Environment Variables

Create or update `.env`:

```bash
MYPROVIDER_API_KEY=your-api-key
MYPROVIDER_REGION=us-east-1
```

## Configuration

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `NOSANA_SIDECAR_URL` | Nosana sidecar endpoint | `http://localhost:3000` |
| `NOSANA_DISCOVERY_URL` | Nosana market discovery URL | `https://dashboard.k8s.prd.nos.ci/api/markets` |
| `AKASH_SIDECAR_URL` | Akash sidecar endpoint | `http://localhost:3000/akash` |
| `AKASH_NODE` | Akash RPC endpoint | `https://rpc.akash.forbole.com:443` |
| `EPHEMERAL_FAILURE_THRESHOLD_MINUTES` | Quick failure detection for ephemeral providers | `10` |
| `DEFAULT_READINESS_TIMEOUT` | Default timeout for node readiness | `300` |
| `DEFAULT_POLLING_INTERVAL` | Default polling interval | `20` |

## API Endpoints

### List Provider Resources

```http
GET /provider/resources?provider={provider}
```

Returns resources for a specific provider or all providers if none specified.

### List Registered Providers

```http
GET /inventory/providers
```

Returns all registered providers and their capabilities.

## Best Practices

### 1. Use Capabilities for Provider-Agnostic Logic

```python
# Good
capabilities = adapter.get_capabilities()
if capabilities.is_ephemeral:
    await inventory.mark_terminated(node_id)

# Avoid
if provider == "nosana":  # Provider-specific logic
    await inventory.mark_terminated(node_id)
```

### 2. Handle Provider-Specific Timeouts

```python
timeout = adapter.get_readiness_timeout()  # Uses capabilities
expose_url = await adapter.wait_for_ready(
    provider_instance_id=instance_id,
    timeout=timeout
)
```

### 3. Standardize Error Handling

All adapters should raise `RuntimeError` for provisioning failures with descriptive messages.

### 4. Implement Graceful Degradation

If log streaming isn't supported, return an appropriate response:

```python
async def get_log_streaming_info(self, *, provider_instance_id: str) -> Dict:
    return {
        "ws_url": None,
        "supported": False,
        "message": "Log streaming not yet implemented for {provider}"
    }
```

## Testing

### Unit Tests

Create tests for each adapter method:

```python
@pytest.mark.asyncio
async def test_nosana_adapter_discover_resources():
    adapter = NosanaAdapter()
    resources = await adapter.discover_resources()
    assert len(resources) > 0
    assert all("provider" in r for r in resources)
```

### Integration Tests

Test with real or mocked provider APIs:

```python
@pytest.mark.integration
async def test_akash_provision_and_deprovision():
    adapter = AkashAdapter()
    
    # Provision
    node_spec = await adapter.provision_node(
        provider_resource_id="akash-gpu-market",
        pool_id="test-pool",
        metadata={"image": "test-image"}
    )
    
    # Verify
    assert node_spec["provider"] == "akash"
    
    # Cleanup
    await adapter.deprovision_node(
        provider_instance_id=node_spec["provider_instance_id"]
    )
```

## Migration Guide

### From Provider-Specific Code

**Before:**
```python
if provider == "nosana":
    timeout = 300
    is_ephemeral = True
```

**After:**
```python
adapter = get_adapter(provider)
capabilities = adapter.get_capabilities()
timeout = capabilities.readiness_timeout_seconds
is_ephemeral = capabilities.is_ephemeral
```

## Troubleshooting

### Common Issues

1. **Adapter not found**: Ensure the adapter is registered in `registry.py`
2. **Missing capabilities**: All adapters must define `CAPABILITIES` class attribute
3. **Timeout issues**: Use adapter-specific timeouts from capabilities instead of hardcoded values
4. **Log streaming not working**: Check if `supports_log_streaming` capability is set correctly

## Contributing

When adding new adapters:

1. Follow the existing adapter patterns
2. Define comprehensive capabilities
3. Add configuration options to `config.py`
4. Update this documentation
5. Add unit and integration tests
6. Ensure backward compatibility if modifying existing adapters

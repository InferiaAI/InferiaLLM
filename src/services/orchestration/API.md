# Compute Abstraction Layer - API Reference

## Overview

This document describes the REST API endpoints for the Compute Abstraction Layer following the refactoring to support standardized, provider-agnostic compute provisioning.

## Base URL

```
http://localhost:8080  # HTTP API
```

## Authentication

All endpoints require appropriate authentication via API keys or session tokens (implementation-specific).

## Endpoints

### Provider Management

#### List Provider Resources

```http
GET /provider/resources?provider={provider}
```

Returns available compute resources from registered providers.

**Query Parameters:**
- `provider` (optional): Filter by specific provider (e.g., "nosana", "akash", "k8s")
  - If omitted, returns resources from all registered providers

**Response:**
```json
{
  "resources": [
    {
      "provider": "nosana",
      "provider_resource_id": "nosana-rtx3090",
      "gpu_type": "rtx3090",
      "gpu_count": 1,
      "gpu_memory_gb": 24,
      "vcpu": 8,
      "ram_gb": 32,
      "region": "global",
      "pricing_model": "fixed",
      "price_per_hour": 0.50,
      "metadata": {
        "market_address": "..."
      },
      "_provider": "nosana"
    }
  ],
  "errors": [
    {
      "provider": "akash",
      "error": "Connection timeout"
    }
  ]
}
```

**Status Codes:**
- `200`: Success
- `400`: Invalid provider specified
- `500`: Internal server error

---

### Inventory Management

#### Heartbeat

```http
POST /inventory/heartbeat
```

Updates node status and triggers deployment state synchronization.

**Request Body:**
```json
{
  "provider": "nosana",
  "provider_instance_id": "job-address-123",
  "gpu_allocated": 1,
  "vcpu_allocated": 8,
  "ram_gb_allocated": 32,
  "health_score": 100,
  "state": "READY",
  "expose_url": "https://node-123.nosana.io"
}
```

**States:**
- `READY`: Node is operational
- `BUSY`: Node is running workloads
- `UNHEALTHY`: Node has issues
- `TERMINATED`: Node is stopped
- `FAILED`: Node failed (ephemeral providers only)

**Response:**
```json
{
  "status": "ok"
}
```

**Status Codes:**
- `200`: Success
- `404`: Node not found
- `500`: Internal server error

---

#### List Nodes by Provider

```http
GET /inventory/nodes/{provider}
```

Returns all nodes registered for a specific provider.

**Path Parameters:**
- `provider` (required): Provider name (e.g., "nosana", "akash", "k8s")

**Response:**
```json
{
  "nodes": [
    {
      "id": "uuid",
      "pool_id": "uuid",
      "provider": "nosana",
      "provider_instance_id": "job-address-123",
      "hostname": "nosana-abc123",
      "gpu_total": 1,
      "gpu_allocated": 1,
      "vcpu_total": 8,
      "vcpu_allocated": 8,
      "ram_gb_total": 32,
      "ram_gb_allocated": 32,
      "state": "ready",
      "health_score": 100,
      "expose_url": "https://node-123.nosana.io",
      "last_heartbeat": "2026-02-18T12:00:00Z",
      "created_at": "2026-02-18T10:00:00Z",
      "updated_at": "2026-02-18T12:00:00Z"
    }
  ]
}
```

**Status Codes:**
- `200`: Success
- `500`: Internal server error

---

#### List Registered Providers

```http
GET /inventory/providers
```

Returns all registered providers and their capabilities.

**Response:**
```json
{
  "providers": {
    "nosana": {
      "adapter_type": "depin",
      "capabilities": {
        "supports_log_streaming": true,
        "supports_confidential_compute": true,
        "supports_spot_instances": false,
        "supports_multi_gpu": true,
        "is_ephemeral": true,
        "requires_readiness_poll": true,
        "readiness_timeout_seconds": 300,
        "polling_interval_seconds": 20,
        "requires_sidecar": true,
        "supports_direct_provisioning": true,
        "pricing_model": "fixed",
        "features": {
          "job_based": true,
          "market_based_pricing": true,
          "blockchain_backed": true
        }
      }
    },
    "akash": {
      "adapter_type": "depin",
      "capabilities": {
        "supports_log_streaming": false,
        "is_ephemeral": true,
        "pricing_model": "auction",
        "features": {
          "sdl_based": true,
          "auction_based_pricing": true
        }
      }
    },
    "k8s": {
      "adapter_type": "on_prem",
      "capabilities": {
        "is_ephemeral": false,
        "pricing_model": "fixed",
        "features": {
          "native_k8s": true,
          "pod_based": true
        }
      }
    }
  }
}
```

**Status Codes:**
- `200`: Success
- `500`: Internal server error

---

### Compute Pool Management

#### Create Pool

```http
POST /createpool
```

Creates a new compute pool for resource allocation.

**Request Body:**
```json
{
  "pool_name": "my-gpu-pool",
  "owner_type": "user",
  "owner_id": "user-123",
  "provider": "nosana",
  "allowed_gpu_types": ["rtx3090", "rtx4090"],
  "max_cost_per_hour": 1.00,
  "is_dedicated": false,
  "provider_pool_id": "market-address-123",
  "scheduling_policy_json": "{}"
}
```

**Validation:**
- Provider must be registered in the adapter system
- Provider_pool_id format validated based on provider type

**Response:**
```json
{
  "pool_id": "uuid",
  "status": "CREATED"
}
```

**Status Codes:**
- `200`: Success
- `400`: Invalid provider or validation error
- `409`: Pool already exists
- `500`: Internal server error

---

#### List Pools

```http
GET /listPools/{owner_id}
```

Returns all compute pools for an owner.

**Path Parameters:**
- `owner_id` (required): Owner identifier

**Response:**
```json
{
  "pools": [
    {
      "pool_id": "uuid",
      "pool_name": "my-gpu-pool",
      "provider": "nosana",
      "state": "ACTIVE",
      "allowed_gpu_types": ["rtx3090"],
      "created_at": "2026-02-18T10:00:00Z"
    }
  ]
}
```

---

#### Delete Pool

```http
POST /deletepool/{pool_id}
```

Deletes a compute pool.

**Path Parameters:**
- `pool_id` (required): Pool identifier

**Response:**
```json
{
  "pool_id": "uuid",
  "status": "DELETED"
}
```

---

### Deployment Management

#### Deploy Model

```http
POST /deploy
```

Creates a new model deployment.

**Request Body:**
```json
{
  "pool_id": "uuid",
  "model_id": "optional-model-uuid",
  "model_name": "llama-2-7b",
  "model_version": "v1.0",
  "replicas": 1,
  "gpu_per_replica": 1,
  "configuration": {
    "image": "vllm/vllm-openai:latest",
    "engine": "vllm",
    "workload_type": "inference"
  }
}
```

**Response:**
```json
{
  "deployment_id": "uuid",
  "status": "PENDING"
}
```

---

#### Get Deployment Status

```http
GET /status/{deployment_id}
```

Returns deployment status and details.

**Path Parameters:**
- `deployment_id` (required): Deployment identifier

**Response:**
```json
{
  "deployment_id": "uuid",
  "state": "RUNNING",
  "model_name": "llama-2-7b",
  "endpoint": "https://node-123.nosana.io",
  "pool_id": "uuid",
  "replicas": 1,
  "created_at": "2026-02-18T10:00:00Z"
}
```

---

#### Terminate Deployment

```http
POST /terminate
```

Terminates a running deployment.

**Request Body:**
```json
{
  "deployment_id": "uuid"
}
```

**Response:**
```json
{
  "deployment_id": "uuid",
  "status": "TERMINATING"
}
```

---

#### List Deployments

```http
GET /listDeployments/{pool_id}
```

Returns all deployments for a pool.

**Path Parameters:**
- `pool_id` (required): Pool identifier

**Response:**
```json
{
  "deployments": [
    {
      "deployment_id": "uuid",
      "model_name": "llama-2-7b",
      "state": "RUNNING",
      "replicas": 1,
      "endpoint": "https://node-123.nosana.io",
      "created_at": "2026-02-18T10:00:00Z"
    }
  ]
}
```

---

### Logs

#### Get Deployment Logs

```http
GET /logs/{deployment_id}
```

Returns logs for a deployment.

**Path Parameters:**
- `deployment_id` (required): Deployment identifier

**Query Parameters:**
- `tail` (optional): Number of lines to return (default: 100)

**Response:**
```json
{
  "logs": [
    "2026-02-18 12:00:00 INFO: Server starting...",
    "2026-02-18 12:00:01 INFO: Model loaded successfully",
    "2026-02-18 12:00:02 INFO: Listening on port 8000"
  ],
  "provider": "nosana"
}
```

**Status Codes:**
- `200`: Success
- `404`: Deployment not found
- `500`: Failed to fetch logs

---

#### Stream Deployment Logs (WebSocket)

```http
GET /logs/{deployment_id}/stream
```

Returns WebSocket connection details for real-time log streaming.

**Path Parameters:**
- `deployment_id` (required): Deployment identifier

**Response:**
```json
{
  "ws_url": "ws://localhost:3000/nosana",
  "provider": "nosana",
  "subscription": {
    "type": "subscribe_logs",
    "jobId": "job-address-123",
    "nodeAddress": "node-address-456"
  },
  "supported": true
}
```

**Note:** If provider doesn't support streaming:
```json
{
  "ws_url": null,
  "supported": false,
  "message": "Log streaming not yet implemented for {provider}"
}
```

---

## Error Handling

All endpoints follow standard HTTP status codes:

- `200 OK`: Request successful
- `400 Bad Request`: Invalid request parameters
- `404 Not Found`: Resource not found
- `409 Conflict`: Resource already exists
- `500 Internal Server Error`: Server-side error

Error responses include descriptive messages:

```json
{
  "detail": "No adapter registered for provider 'unknown'. Available providers: ['nosana', 'akash', 'k8s']"
}
```

## Provider-Agnostic Design

The API has been refactored to be provider-agnostic:

1. **No hardcoded provider names**: All provider-specific logic uses the capabilities system
2. **Standardized responses**: All providers return resources in the same format
3. **Capability discovery**: `/inventory/providers` exposes what each provider supports
4. **Unified error handling**: Consistent error messages across all providers

## Migration Notes

### Before (Provider-Specific)
```
GET /provider/resources  # Always defaulted to nosana
```

### After (Provider-Agnostic)
```
GET /provider/resources              # Returns all providers
GET /provider/resources?provider=X   # Returns specific provider
```

### Before (Hardcoded Logic)
```python
if provider == "nosana":
    timeout = 300
```

### After (Capability-Based)
```python
adapter = get_adapter(provider)
timeout = adapter.get_capabilities().readiness_timeout_seconds
```

## Testing Examples

### cURL Examples

**List all provider resources:**
```bash
curl http://localhost:8080/provider/resources
```

**List specific provider:**
```bash
curl http://localhost:8080/provider/resources?provider=nosana
```

**Get provider capabilities:**
```bash
curl http://localhost:8080/inventory/providers
```

**Create pool:**
```bash
curl -X POST http://localhost:8080/createpool \
  -H "Content-Type: application/json" \
  -d '{
    "pool_name": "test-pool",
    "owner_type": "user",
    "owner_id": "user-123",
    "provider": "nosana",
    "allowed_gpu_types": ["rtx3090"],
    "max_cost_per_hour": 0.50,
    "is_dedicated": false,
    "provider_pool_id": "market-123",
    "scheduling_policy_json": "{}"
  }'
```

**Send heartbeat:**
```bash
curl -X POST http://localhost:8080/inventory/heartbeat \
  -H "Content-Type: application/json" \
  -d '{
    "provider": "nosana",
    "provider_instance_id": "job-123",
    "state": "READY",
    "expose_url": "https://node-123.nosana.io"
  }'
```

## Rate Limiting

Rate limits may apply to certain endpoints depending on your deployment configuration.

## Changelog

### v2.0.0 (Refactored)
- Added `/inventory/providers` endpoint for capability discovery
- Modified `/provider/resources` to support all providers
- Added provider validation to pool creation
- Removed hardcoded Nosana defaults
- Implemented capability-based ephemeral detection

### v1.0.0 (Legacy)
- Initial API with Nosana-specific implementation

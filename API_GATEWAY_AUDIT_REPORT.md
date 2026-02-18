# API Gateway Architecture Audit Report
## InferiaLLM Platform

**Date:** 2026-02-18  
**Auditor:** AI Assistant  
**Scope:** Services architecture, API endpoints, and gateway patterns

---

## Executive Summary

The InferiaLLM platform currently operates with a **decentralized service access pattern** where the dashboard communicates directly with multiple backend services. While the **Filtration Service** already contains robust gateway functionality, the dashboard bypasses it for several operations, creating:

- ❌ **Security inconsistencies** - Multiple authentication entry points
- ❌ **CORS complexity** - Each service needs CORS configuration
- ❌ **Client complexity** - Dashboard maintains multiple service clients
- ❌ **Rate limiting gaps** - Not all endpoints protected uniformly
- ❌ **Operational overhead** - Managing multiple service URLs

**Recommendation:** Consolidate ALL dashboard-to-service communication through the Filtration Gateway as the single API Gateway.

---

## 1. Current Service Architecture

### 1.1 Services Overview

| Service | Port | Purpose | Dashboard Access |
|---------|------|---------|------------------|
| **Filtration** | 8000 | Policy, Auth, Management | Direct ✅ |
| **Inference** | 8001 | LLM Data Plane | Direct ✅ |
| **Orchestration** | 8080 | Compute Management | Direct ✅ |
| **Guardrail** | 8002 | Safety Scanning | Direct ✅ |
| **Data** | 8003 | Knowledge Base | Direct ✅ |

### 1.2 Current Dashboard Communication Pattern

```
┌─────────────────────────────────────────────────────────────┐
│                      DASHBOARD (Port 3001)                  │
└─────────────────────────────────────────────────────────────┘
                              │
        ┌─────────────────────┼─────────────────────┐
        │                     │                     │
        ▼                     ▼                     ▼
┌──────────────┐    ┌──────────────┐    ┌──────────────┐
│ Filtration   │    │ Orchestration│    │   Others     │
│  :8000       │    │  :8080       │    │  :8002/8003  │
└──────────────┘    └──────────────┘    └──────────────┘
```

**Problem:** Dashboard maintains separate clients for each service, bypassing the gateway for non-management operations.

---

## 2. API Endpoints Inventory

### 2.1 Filtration Service (Port 8000) - Currently Gateway

**Authentication Endpoints:**
```
POST   /auth/login
POST   /auth/register-invite
POST   /auth/accept-invite
POST   /auth/refresh
POST   /auth/switch-org
GET    /auth/organizations
GET    /auth/me
POST   /auth/totp/setup
POST   /auth/totp/verify
POST   /auth/totp/disable
GET    /auth/invitations/{token}
```

**Management Endpoints (Dashboard):**
```
GET    /management/organizations
PUT    /management/organizations
GET    /management/users
GET    /management/deployments
POST   /management/deployments
GET    /management/api-keys
POST   /management/api-keys
GET    /management/config/providers
POST   /management/config/providers
GET    /management/knowledge-base/*
POST   /management/knowledge-base/*
GET    /management/prompts/*
POST   /management/prompts/*
GET    /management/insights/summary
GET    /management/insights/timeseries
GET    /management/insights/logs
GET    /management/insights/filters
GET    /management/insights/top-ips
GET    /management/insights/top-models
GET    /management/invitations
POST   /management/invitations
DELETE /management/invitations/{id}
```

**RBAC Admin Endpoints:**
```
GET    /admin/roles
POST   /admin/roles
PUT    /admin/roles/{name}
DELETE /admin/roles/{name}
GET    /admin/roles/permissions/list
PUT    /admin/users/{user_id}/role
```

**Audit Endpoints:**
```
GET    /audit/logs
```

**Internal Endpoints (Service-to-Service):**
```
POST   /internal/policy/check_quota
POST   /internal/policy/track_usage
POST   /internal/logs/create
POST   /internal/guardrails/scan
GET    /internal/models
POST   /internal/context/resolve
POST   /internal/prompt/process
GET    /internal/config/provider
```

**Current Gateway Capabilities:**
- ✅ JWT authentication middleware
- ✅ Rate limiting (Redis-backed token bucket)
- ✅ CORS configuration
- ✅ Internal API key validation
- ✅ Request ID tracking

### 2.2 Inference Service (Port 8001)

**OpenAI-Compatible API:**
```
GET    /v1/models
POST   /v1/chat/completions
```

**Current Flow:**
```
Client → Inference Gateway → Filtration (auth/policy) → Backend Model
```

### 2.3 Orchestration Service (Port 8080)

**Compute Management:**
```
GET    /health
GET    /provider/resources
POST   /createpool
GET    /listPools/{owner_id}
POST   /deletepool/{pool_id}
POST   /deploy
GET    /status/{deployment_id}
POST   /terminate
GET    /listDeployments/{pool_id}
GET    /logs/{deployment_id}
GET    /logs/{deployment_id}/stream (WebSocket)
GET    /inventory/providers
GET    /inventory/nodes/{provider}
POST   /inventory/heartbeat
```

### 2.4 Guardrail Service (Port 8002)

**Safety Scanning:**
- Text scanning via `/scan` endpoint
- PII detection
- Provider: LLM Guard, Llama Guard, Lakera

### 2.5 Data Service (Port 8003)

**Knowledge Base:**
- Document ingestion
- Vector operations (ChromaDB)
- Context assembly
- Prompt template rendering

---

## 3. Current Dashboard API Client Configuration

**File:** `apps/dashboard/src/lib/api.ts`

```typescript
const API_CONFIG = {
  MANAGEMENT_URL:  "http://localhost:8000",   // Filtration
  COMPUTE_URL:     "http://localhost:8080",   // Orchestration
  WEB_SOCKET_URL:  "ws://localhost:3000",     // DePIN Sidecar
  INFERENCE_URL:   "http://localhost:8001",   // Inference
  SIDECAR_URL:     "http://localhost:3000",   // DePIN Sidecar
  DATA_URL:        "http://localhost:8003",   // Data Service
  GUARDRAIL_URL:   "http://localhost:8002",   // Guardrail Service
};
```

**Service Modules Directly Accessed:**
- `authService.ts` → Filtration :8000
- `configService.ts` → Filtration :8000
- `rbacService.ts` → Filtration :8000
- `auditService.ts` → Filtration :8000
- `insightsService.ts` → Filtration :8000
- **BUT ALSO:**
  - Deployment operations → Orchestration :8080 (direct)
  - Log streaming → Orchestration :8080 (direct WebSocket)
  - Provider config → Potentially multiple services

---

## 4. Issues with Current Architecture

### 4.1 Security Concerns

| Issue | Risk Level | Description |
|-------|------------|-------------|
| **Multiple Auth Entry Points** | 🔴 High | Orchestration/Guardrail/Data lack proper JWT validation |
| **CORS Misconfiguration Risk** | 🟡 Medium | Each service needs CORS config, easy to miss one |
| **Internal API Exposure** | 🟡 Medium | Services expose ports that should be internal-only |
| **Token Validation Inconsistency** | 🟡 Medium | Different services may validate tokens differently |

### 4.2 Operational Concerns

| Issue | Impact | Description |
|-------|--------|-------------|
| **Service Discovery Complexity** | 🟡 Medium | Dashboard needs URLs for N services |
| **Rate Limiting Inconsistency** | 🔴 High | Not all endpoints protected |
| **SSL/TLS Management** | 🟡 Medium | Multiple certificates needed |
| **Monitoring Complexity** | 🟡 Medium | Metrics spread across services |

### 4.3 Developer Experience

| Issue | Impact | Description |
|-------|--------|-------------|
| **Client SDK Complexity** | 🟡 Medium | Must maintain multiple API clients |
| **API Documentation** | 🟡 Medium | Multiple OpenAPI specs needed |
| **Testing Overhead** | 🟡 Medium | Integration tests across services |

---

## 5. Proposed API Gateway Architecture

### 5.1 Target Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                      DASHBOARD (Port 3001)                  │
│              Single Client → API Gateway Only               │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│              API GATEWAY (Filtration :8000)                 │
│  ┌──────────────┬──────────────┬──────────────┐            │
│  │   Auth       │   Proxy      │   Router     │            │
│  │ Middleware   │ Middleware   │ Middleware   │            │
│  └──────────────┴──────────────┴──────────────┘            │
└─────────────────────────────────────────────────────────────┘
                              │
        ┌─────────────────────┼─────────────────────┐
        │                     │                     │
        ▼                     ▼                     ▼
┌──────────────┐    ┌──────────────┐    ┌──────────────┐
│ Inference    │    │Orchestration │    │  Guardrail   │
│  :8001       │    │  :8080       │    │  :8002       │
│  (internal)  │    │  (internal)  │    │  (internal)  │
└──────────────┘    └──────────────┘    └──────────────┘
                              │
                              ▼
                    ┌──────────────┐
                    │    Data      │
                    │   :8003      │
                    │  (internal)  │
                    └──────────────┘
```

### 5.2 Gateway Routing Rules

| Dashboard Need | Current Endpoint | Gateway Route | Proxied To |
|----------------|------------------|---------------|------------|
| Auth operations | `/auth/*` | `/api/v1/auth/*` | Filtration internal |
| User management | `/management/users` | `/api/v1/users/*` | Filtration internal |
| Deployments | `/deploy` | `/api/v1/deployments/*` | Orchestration :8080 |
| Deployment logs | `/logs/{id}/stream` | `/api/v1/deployments/{id}/logs` | Orchestration :8080 |
| Compute pools | `/createpool` | `/api/v1/pools/*` | Orchestration :8080 |
| Provider resources | `/provider/resources` | `/api/v1/providers/resources` | Orchestration :8080 |
| Inference | `/v1/chat/completions` | `/api/v1/inference/*` | Inference :8001 |
| Models list | `/v1/models` | `/api/v1/inference/models` | Inference :8001 |
| Audit logs | `/audit/logs` | `/api/v1/audit/logs` | Filtration internal |
| API Keys | `/management/api-keys` | `/api/v1/api-keys/*` | Filtration internal |
| Organizations | `/management/organizations` | `/api/v1/organizations/*` | Filtration internal |
| Configs | `/management/config/*` | `/api/v1/config/*` | Filtration internal |
| Insights | `/management/insights/*` | `/api/v1/insights/*` | Filtration internal |
| RBAC | `/admin/*` | `/api/v1/admin/*` | Filtration internal |

### 5.3 Unified API Structure

All dashboard operations go through a single base URL:

```
https://api.inferia.io/api/v1/
```

**Route Prefixes:**
- `/api/v1/auth/*` - Authentication
- `/api/v1/users/*` - User management
- `/api/v1/organizations/*` - Organization management
- `/api/v1/deployments/*` - Deployment operations
- `/api/v1/pools/*` - Compute pools
- `/api/v1/inference/*` - LLM inference
- `/api/v1/insights/*` - Analytics
- `/api/v1/audit/*` - Audit logs
- `/api/v1/admin/*` - RBAC administration
- `/api/v1/config/*` - Configuration

---

## 6. Implementation Recommendations

### 6.1 Phase 1: Gateway Enhancement (Priority: HIGH)

**Tasks:**
1. **Add Proxy Routes to Filtration Gateway**
   - Create `/api/v1/deployments/*` → Orchestration :8080
   - Create `/api/v1/pools/*` → Orchestration :8080
   - Create `/api/v1/inference/*` → Inference :8001
   - Create `/api/v1/guardrails/*` → Guardrail :8002 (if needed)
   - Create `/api/v1/data/*` → Data :8003 (if needed)

2. **Implement Request Proxy Middleware**
   - Path-based routing
   - Header forwarding (Authorization, X-Request-ID)
   - Response streaming for WebSocket endpoints
   - Error handling and transformation

3. **Consolidate Authentication**
   - Single JWT validation at gateway
   - Forward user context in headers to services
   - Remove auth from individual services (internal only)

### 6.2 Phase 2: Dashboard Updates (Priority: HIGH)

**Tasks:**
1. **Update API Client**
   - Change from multi-service to single gateway
   - Remove service-specific configurations
   - Update all service calls to use gateway routes

2. **Update Frontend Code**
   - Replace direct orchestration calls with gateway calls
   - Update WebSocket connections to go through gateway
   - Handle unified error responses

**Before:**
```typescript
// Current - Multiple service clients
const deployment = await fetch(`${COMPUTE_URL}/deploy`, {...});
const models = await fetch(`${INFERENCE_URL}/v1/models`, {...});
const logs = new WebSocket(`${WEB_SOCKET_URL}/logs/${id}/stream`);
```

**After:**
```typescript
// Proposed - Single gateway client
const deployment = await fetch(`${GATEWAY_URL}/api/v1/deployments`, {...});
const models = await fetch(`${GATEWAY_URL}/api/v1/inference/models`, {...});
const logs = new WebSocket(`${GATEWAY_URL}/api/v1/deployments/${id}/logs`);
```

### 6.3 Phase 3: Service Hardening (Priority: MEDIUM)

**Tasks:**
1. **Internal Network Isolation**
   - Orchestration, Inference, Guardrail, Data only accessible via gateway
   - Remove public ports (keep only gateway :8000 public)
   - Use Docker internal networking

2. **Update Internal Authentication**
   - Services only accept `X-Internal-API-Key`
   - Remove JWT validation from downstream services
   - Trust gateway-forwarded user context

3. **Add Gateway-Level Features**
   - Request/response logging
   - Metrics collection (Prometheus)
   - Circuit breaker patterns
   - Request deduplication
   - Response caching (where appropriate)

### 6.4 Phase 4: Advanced Gateway Features (Priority: LOW)

**Future Enhancements:**
- API versioning (`/api/v2/`)
- Request transformation
- GraphQL gateway (optional)
- API key management portal
- Developer documentation portal
- Rate limit dashboards

---

## 7. Security Benefits

### 7.1 Authentication Consolidation

| Before | After |
|--------|-------|
| JWT validation in multiple services | JWT validation only in gateway |
| Multiple token refresh endpoints | Single refresh endpoint |
| Inconsistent session handling | Unified session management |

### 7.2 Authorization Enforcement

| Before | After |
|--------|-------|
| RBAC checks scattered | Centralized RBAC in gateway |
| Permission gaps likely | Uniform permission model |
| Hard to audit | Single point of audit |

### 7.3 Network Security

| Before | After |
|--------|-------|
| Services exposed on public ports | Only gateway exposed publicly |
| Multiple SSL certificates needed | Single SSL endpoint |
| Complex firewall rules | Single firewall rule |

---

## 8. Operational Benefits

### 8.1 Simplified Configuration

| Before | After |
|--------|-------|
| N service URLs in dashboard config | 1 gateway URL |
| CORS config per service | Single CORS config |
| Multiple SSL certs | Single SSL cert |

### 8.2 Improved Observability

| Before | After |
|--------|-------|
| Logs scattered across services | Request tracing through gateway |
| Metrics in different formats | Unified metrics export |
| Hard to correlate requests | Request ID propagation |

### 8.3 Rate Limiting

| Before | After |
|--------|-------|
| Inconsistent limits | Unified rate limiting |
| Redis connections per service | Single Redis connection |
| Bypass possible on some endpoints | All requests go through gateway |

---

## 9. Migration Strategy

### 9.1 Backward Compatibility

**Option A: Gradual Migration (Recommended)**
1. Keep existing endpoints working
2. Add new gateway routes alongside
3. Gradually migrate dashboard calls
4. Deprecate old endpoints after migration

**Option B: Big Bang**
- Higher risk
- Requires coordination
- All services updated simultaneously

### 9.2 Implementation Timeline

**Week 1-2: Gateway Development**
- Add proxy middleware
- Configure routes
- Test internally

**Week 3: Dashboard Updates**
- Update API client
- Migrate service calls
- Testing

**Week 4: Security Hardening**
- Close service ports
- Update internal auth
- Security testing

**Week 5: Monitoring & Documentation**
- Add metrics
- Update API docs
- Create runbooks

---

## 10. Code Changes Required

### 10.1 Filtration Gateway - New Files

**File:** `package/src/inferia/services/filtration/gateway/proxy_routes.py`
```python
"""
API Gateway proxy routes for downstream services.
"""

from fastapi import APIRouter, Request, Response, Depends
import httpx
from typing import Optional

router = APIRouter(prefix="/api/v1")

# Service URLs (from environment/config)
ORCHESTRATION_URL = "http://localhost:8080"
INFERENCE_URL = "http://localhost:8001"

@router.api_route("/deployments/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def proxy_deployments(request: Request, path: str):
    """Proxy deployment operations to orchestration service."""
    ...

@router.api_route("/pools/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def proxy_pools(request: Request, path: str):
    """Proxy compute pool operations to orchestration service."""
    ...

@router.api_route("/inference/{path:path}", methods=["GET", "POST"])
async def proxy_inference(request: Request, path: str):
    """Proxy inference operations to inference service."""
    ...
```

**File:** `package/src/inferia/services/filtration/gateway/proxy_middleware.py`
```python
"""
Request/response proxy middleware for API gateway.
"""

class ProxyMiddleware:
    """Handles proxying requests to downstream services."""
    
    async def forward_request(
        self,
        target_url: str,
        request: Request,
        headers: Optional[dict] = None
    ) -> Response:
        """Forward request to target service."""
        ...
    
    async def forward_stream(
        self,
        target_url: str,
        request: Request
    ) -> StreamingResponse:
        """Forward streaming request (for WebSocket/SSE)."""
        ...
```

### 10.2 Dashboard - Updated Files

**File:** `apps/dashboard/src/lib/api.ts`
```typescript
// BEFORE - Multiple services
export const API_CONFIG = {
  MANAGEMENT_URL: "http://localhost:8000",
  COMPUTE_URL: "http://localhost:8080",
  INFERENCE_URL: "http://localhost:8001",
  // ... more URLs
};

// AFTER - Single gateway
export const API_CONFIG = {
  GATEWAY_URL: "http://localhost:8000/api/v1",  // Single endpoint
};

// Unified API client
export const apiClient = axios.create({
  baseURL: API_CONFIG.GATEWAY_URL,
  headers: {
    'Content-Type': 'application/json',
  },
});
```

**File:** `apps/dashboard/src/services/deploymentService.ts`
```typescript
// BEFORE - Direct orchestration call
export const createDeployment = async (data: DeploymentData) => {
  const response = await fetch(`${COMPUTE_URL}/deploy`, {...});
  return response.json();
};

// AFTER - Through gateway
export const createDeployment = async (data: DeploymentData) => {
  const response = await apiClient.post('/deployments', data);
  return response.data;
};
```

---

## 11. Risk Assessment

### 11.1 Low Risk

- ✅ Gateway already exists with auth/rate limiting
- ✅ Filtration service is stable and well-tested
- ✅ Internal API key pattern already established

### 11.2 Medium Risk

- ⚠️ WebSocket proxying complexity
- ⚠️ Response streaming performance
- ⚠️ Dashboard code refactoring effort

### 11.3 Mitigation Strategies

1. **Extensive Testing**
   - Unit tests for proxy middleware
   - Integration tests for all routes
   - Load testing for performance

2. **Gradual Rollout**
   - Start with non-critical endpoints
   - Feature flags for new routes
   - Rollback capability

3. **Monitoring**
   - Add comprehensive logging
   - Set up alerts for errors
   - Track latency metrics

---

## 12. Conclusion

### 12.1 Current State Summary

The InferiaLLM platform has a solid foundation with the Filtration Service containing robust gateway functionality. However, the dashboard currently bypasses the gateway for several services, creating security inconsistencies and operational complexity.

### 12.2 Recommendation

**Proceed with API Gateway consolidation.** The benefits outweigh the risks:

- 🔒 **Improved security** - Single authentication entry point
- 🚀 **Simplified operations** - One service to manage publicly
- 🛠️ **Better developer experience** - Single API client
- 📊 **Enhanced observability** - Centralized logging and metrics

### 12.3 Next Steps

1. **Review this audit** with the team
2. **Prioritize implementation phases**
3. **Create detailed technical specs** for each phase
4. **Set up testing environment** for gateway routes
5. **Begin Phase 1** (Gateway Enhancement)

---

## Appendix A: Current vs Proposed Comparison

| Aspect | Current | Proposed |
|--------|---------|----------|
| **Public Endpoints** | 5 services × N ports | 1 gateway on port 8000 |
| **Auth Entry Points** | Multiple (Filtration + direct service access) | Single (Gateway only) |
| **Dashboard Clients** | 5+ service clients | 1 unified client |
| **CORS Configs** | 5 separate configs | 1 gateway config |
| **SSL Certificates** | Potentially multiple | Single certificate |
| **Rate Limiting** | Inconsistent | Unified at gateway |
| **Request Tracing** | Difficult across services | Centralized in gateway |

## Appendix B: Service Communication Matrix

### Current Pattern

| From | To | Method | Auth |
|------|-----|--------|------|
| Dashboard | Filtration | HTTP | JWT |
| Dashboard | Orchestration | HTTP | JWT (direct) |
| Dashboard | Inference | HTTP | API Key (direct) |
| Inference | Filtration | HTTP | Internal API Key |
| Filtration | Guardrail | HTTP | Internal API Key |
| Filtration | Data | HTTP | Internal API Key |

### Proposed Pattern

| From | To | Method | Auth |
|------|-----|--------|------|
| Dashboard | Gateway | HTTP | JWT |
| Gateway | Filtration | Internal | Internal API Key |
| Gateway | Orchestration | Internal | Internal API Key + User Context |
| Gateway | Inference | Internal | Internal API Key + User Context |
| Inference | Filtration | Internal | Internal API Key |
| Filtration | Guardrail | Internal | Internal API Key |
| Filtration | Data | Internal | Internal API Key |

---

**End of Report**

*Generated by AI Assistant on 2026-02-18*

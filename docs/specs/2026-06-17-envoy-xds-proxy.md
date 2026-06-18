# Envoy xDS Proxy — Component Boundaries

## Overview

Envoy sits as a layer-7 edge proxy in front of InferiaLLM workers. The inference
gateway routes a worker-hosted request through Envoy by replacing the worker's
advertise URL with `envoy_url` and injecting the `X-Inferia-Route-Cluster`
header. Envoy's cluster-header-based routing dispatches to the right upstream
pool/engine group. Cluster and endpoint membership are served dynamically via
Envoy's xDS protocol (CDS + EDS) by a lightweight control plane.

## Thin-Slice Boundaries

### 1. Static Bootstrap Configuration
| Field | Value |
|-------|-------|
| **File** | `envoy/envoy.yaml` |
| **Doc** | [Bootstrap](https://www.envoyproxy.io/docs/envoy/v1.38.2/configuration/overview/bootstrap), [Listeners](https://www.envoyproxy.io/docs/envoy/v1.38.2/configuration/listeners/listeners), [HCM](https://www.envoyproxy.io/docs/envoy/v1.38.2/configuration/http/http_conn_man) |
| **Scope** | Static listener `:10000` → HCM → CORS + Router filters → `cluster_header` routing. CORS allows cross-origin from dashboard. xDS cluster (`xds_cluster`) points at the control plane. |
| **Mutable by** | PR to this file only. No runtime changes. |

### 2. xDS Control Plane
| Field | Value |
|-------|-------|
| **File** | `src/xds_control_plane/xds_shim.py` |
| **Docs** | [xDS API](https://www.envoyproxy.io/docs/envoy/v1.38.2/configuration/overview/xds_api), [CDS](https://www.envoyproxy.io/docs/envoy/v1.38.2/configuration/upstream/clusters#cluster-discovery-service-cds), [EDS](https://www.envoyproxy.io/docs/envoy/v1.38.2/configuration/upstream/clusters#endpoint-discovery-service-eds), [Management Server](https://www.envoyproxy.io/docs/envoy/v1.38.2/configuration/overview/management_server) |
| **Scope** | FastAPI app at port 18000. Endpoints: CDS `POST /v3/discovery:clusters`, EDS `POST /v3/discovery:endpoints`, `GET /route-table`, `GET /healthz`, `GET /debug/resources`. |
| **Responsibilities** | Polls node source (HTTP from orchestration or local file). Builds per-pool-per-engine clusters named `grp-<pool_id>-<engine>`. Returns 304 when version matches. |

### 3. Docker Compose Wiring
| Field | Value |
|-------|-------|
| **File** | `docker-compose.yml` (`xds-control-plane` + `front-envoy` services, lines ~119-148) |
| **Docs** | [Examples](https://www.envoyproxy.io/docs/envoy/v1.38.2/configuration/overview/examples) |
| **Scope** | Container build/run, port mapping (`:10000` → envoy, `:18000` → xDS), network `inferia-net`, dependency chain. Envoy config mounted as read-only volume. |

### 4. Inference Routing Decision (`worker_routing.py`)
| Field | Value |
|-------|-------|
| **File** | `src/inference/core/worker_routing.py:87-123` (`envoy_route_headers`) |
| **Docs** | [Cluster Header](https://www.envoyproxy.io/docs/envoy/v1.38.2/configuration/http/http_conn_man/routing#config-http-conn-man-route-table-route-cluster-header) |
| **Scope** | Pure function. Given deployment + `envoy_url`, returns `(url, {X-Inferia-Route-Cluster: grp-<pool_id>-<engine>})` or `(None, {})` |

### 5. Envoy URL in Inference Pipeline
| Field | Value |
|-------|-------|
| **Files** | `src/inference/core/pipeline.py:148-153`, `src/inference/core/handlers/completion.py:106-111` |
| **Scope** | Call `envoy_route_headers()`, if envoy_url is returned replace `ctx.endpoint_url` and merge `provider_headers`. |

### 6. Envoy Config Setting
| Field | Value |
|-------|-------|
| **File** | `src/inference/config.py:92-95` (`envoy_url` field, env `ENVOY_URL`) |
| **Scope** | Optional URL. When unset/None, all workers are reached directly. |

### 7. Node Source (Orchestration ↔ xDS)
| Field | Value |
|-------|-------|
| **File** | `src/xds_control_plane/xds_shim.py:99-141` (`HTTPNodeSource`), :239-243 (source selection) |
| **Scope** | Polls `CONTROL_PLANE_URL` every 5s. Fallback reads `nodes.json`. Transforms into Node objects (id, host, port, pool_id, engine, healthy). |
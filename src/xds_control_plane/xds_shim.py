#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlparse

import requests
from fastapi import FastAPI, Request, Response

logger = logging.getLogger("xds_shim")
app = FastAPI(title="InferiaLLM xDS Control Plane", version="0.1.0")

CLUSTER_TYPE_URL = "type.googleapis.com/envoy.config.cluster.v3.Cluster"
ENDPOINT_TYPE_URL = "type.googleapis.com/envoy.config.endpoint.v3.ClusterLoadAssignment"

XDS_CLUSTER_NAME = os.environ.get("XDS_CLUSTER_NAME", "xds_cluster")
REFRESH_DELAY = os.environ.get("XDS_REFRESH_DELAY", "5s")
XDS_PORT = int(os.environ.get("PORT", "18000"))

# Engine types for per-engine cluster generation. Each pool with healthy
# nodes gets one cluster per engine so the inference gateway can route
# by engine type (X-Inferia-Route-Cluster: grp-<pool_id>-<engine>).
# All engine clusters within a pool share the same node endpoints;
# the worker's proxy handles engine-specific routing via
# X-Inferia-Deployment-Id.
KNOWN_ENGINES = {"vllm", "ollama", "worker", "localai"}


@dataclass
class Node:
    node_id: str
    host: str
    port: int
    pool_id: Optional[str] = None
    engine: Optional[str] = None
    healthy: bool = True


def _safe(value: str) -> str:
    return "".join(c if c.isalnum() or c in "-_." else "-" for c in value)


def _parse_advertise_url(advertise_url: str) -> tuple[str, int]:
    if "//" in advertise_url:
        parsed = urlparse(advertise_url)
        return parsed.hostname or "localhost", parsed.port or 8080
    host, _, port = advertise_url.partition(":")
    return host, int(port or 8080)


class FileNodeSource:
    def __init__(self, path: str):
        self.path = path
        self._mtime = 0.0
        self._nodes: list[Node] = []
        self._lock = threading.Lock()

    def nodes(self) -> list[Node]:
        with self._lock:
            try:
                mtime = os.path.getmtime(self.path)
            except OSError:
                return self._nodes
            if mtime != self._mtime:
                self._nodes = self._load()
                self._mtime = mtime
            return self._nodes

    def _load(self) -> list[Node]:
        try:
            with open(self.path) as fh:
                raw = json.load(fh)
        except (FileNotFoundError, json.JSONDecodeError):
            return []
        out = []
        for entry in raw:
            host, port = _parse_advertise_url(entry["advertise_url"])
            out.append(
                Node(
                    node_id=entry["id"],
                    host=host,
                    port=port,
                    pool_id=entry.get("pool_id") or None,
                    engine=entry.get("engine") or None,
                    healthy=entry.get("healthy", True),
                )
            )
        return out


class HTTPNodeSource:
    def __init__(self, url: str, interval_seconds: float = 5.0, timeout: float = 3.0):
        self.url = url
        self.interval = interval_seconds
        self.timeout = timeout
        self._nodes: list[Node] = []
        self._lock = threading.Lock()
        self._stop = False
        threading.Thread(target=self._poll_loop, daemon=True).start()

    def _poll_loop(self):
        while not self._stop:
            try:
                resp = requests.get(self.url, timeout=self.timeout)
                resp.raise_for_status()
                payload = resp.json()
                nodes = payload.get("nodes", payload if isinstance(payload, list) else [])
                parsed = []
                for entry in nodes:
                    host, port = _parse_advertise_url(entry["advertise_url"])
                    parsed.append(
                        Node(
                            node_id=entry["id"],
                            host=host,
                            port=port,
                            pool_id=entry.get("pool_id") or None,
                            engine=entry.get("engine") or None,
                            healthy=entry.get("healthy", True),
                        )
                    )
                with self._lock:
                    self._nodes = parsed
            except Exception as exc:
                logger.warning("HTTPNodeSource poll failed: %s", exc)
            time.sleep(self.interval)

    def nodes(self) -> list[Node]:
        with self._lock:
            return list(self._nodes)


@dataclass
class ResourceSet:
    clusters: dict = field(default_factory=dict)
    endpoints: dict = field(default_factory=dict)
    route_table: dict = field(default_factory=dict)
    cds_version: str = ""
    eds_version: dict = field(default_factory=dict)


def build_cluster(cluster_name: str, members: list[Node]):
    return {
        "@type": CLUSTER_TYPE_URL,
        "name": cluster_name,
        "connect_timeout": "0.25s",
        "lb_policy": "ROUND_ROBIN",
        "type": "EDS",
        "eds_cluster_config": {
            "eds_config": {
                "api_config_source": {
                    "api_type": "REST",
                    "transport_api_version": "V3",
                    "cluster_names": [XDS_CLUSTER_NAME],
                    "refresh_delay": REFRESH_DELAY,
                }
            }
        },
    }


def build_endpoints(cluster_name: str, members: list[Node]):
    return {
        "@type": ENDPOINT_TYPE_URL,
        "cluster_name": cluster_name,
        "endpoints": [
            {
                "lb_endpoints": [
                    {
                        "endpoint": {
                            "address": {
                                "socket_address": {
                                    "address": m.host,
                                    "port_value": m.port,
                                }
                            }
                        }
                    }
                    for m in members
                ]
            }
        ],
    }


def build_resources(nodes: list[Node]) -> ResourceSet:
    rs = ResourceSet()
    healthy = [n for n in nodes if n.healthy]

    # Group by pool_id.
    pools: dict[str, list[Node]] = {}
    singletons: list[Node] = []
    for n in healthy:
        if n.pool_id:
            pools.setdefault(n.pool_id, []).append(n)
        else:
            singletons.append(n)

    # Per-pool engine-filtered clusters — only for engines with active nodes.
    for pool_id, members in pools.items():
        engines_in_pool = {n.engine for n in members if n.engine}
        for engine in engines_in_pool:
            engine_members = [n for n in members if n.engine == engine]
            cluster_name = f"grp-{_safe(pool_id)}-{_safe(engine)}"
            rs.clusters[cluster_name] = build_cluster(cluster_name, engine_members)
            rs.endpoints[cluster_name] = build_endpoints(cluster_name, engine_members)
            rs.eds_version[cluster_name] = _hash(rs.endpoints[cluster_name])
            for n in engine_members:
                rs.route_table[n.node_id] = cluster_name

    # Nodes without a pool_id get their own cluster.
    for n in singletons:
        cluster_name = _safe(n.node_id)
        rs.clusters[cluster_name] = build_cluster(cluster_name, [n])
        rs.endpoints[cluster_name] = build_endpoints(cluster_name, [n])
        rs.eds_version[cluster_name] = _hash(rs.endpoints[cluster_name])
        rs.route_table[n.node_id] = cluster_name

    rs.cds_version = _hash(sorted(rs.clusters.keys()))
    return rs


def _hash(obj) -> str:
    blob = json.dumps(obj, sort_keys=True, default=str).encode()
    return hashlib.sha1(blob).hexdigest()[:12]


NODES_FILE = os.environ.get("NODES_FILE", "/data/nodes.json")
CONTROL_PLANE_URL = os.environ.get("CONTROL_PLANE_URL")

SOURCE: FileNodeSource | HTTPNodeSource = (
    HTTPNodeSource(CONTROL_PLANE_URL) if CONTROL_PLANE_URL else FileNodeSource(NODES_FILE)
)

_state_lock = threading.Lock()
_state = build_resources(SOURCE.nodes())
_state_fingerprint = ""


def _nodes_fingerprint(nodes: list[Node]) -> str:
    """Fast fingerprint to detect node set changes without deep comparison."""
    blob = tuple(sorted((n.node_id, n.pool_id, n.engine, n.healthy) for n in nodes))
    return hashlib.sha1(str(blob).encode()).hexdigest()[:12]


def current_state() -> ResourceSet:
    global _state, _state_fingerprint
    with _state_lock:
        nodes = SOURCE.nodes()
        fp = _nodes_fingerprint(nodes)
        if fp != _state_fingerprint:
            _state = build_resources(nodes)
            _state_fingerprint = fp
        return _state


def _discovery_response(version: str, resources: list, type_url: str):
    return {
        "version_info": version,
        "resources": resources,
        "type_url": type_url,
        "nonce": uuid.uuid4().hex,
    }


@app.post("/v3/discovery:clusters")
async def discovery_clusters(request: Request):
    body = await request.json() or {}
    client_version = body.get("version_info", "")
    state = current_state()
    if client_version == state.cds_version:
        return Response(status_code=304)
    return _discovery_response(
        state.cds_version, list(state.clusters.values()), CLUSTER_TYPE_URL
    )


@app.post("/v3/discovery:endpoints")
async def discovery_endpoints(request: Request):
    body = await request.json() or {}
    client_version = body.get("version_info", "")
    names = body.get("resource_names") or []
    state = current_state()

    wanted = names or list(state.endpoints.keys())
    combined_version = _hash(sorted((n, state.eds_version.get(n, "")) for n in wanted))
    if client_version == combined_version:
        return Response(status_code=304)

    resources = [state.endpoints[n] for n in wanted if n in state.endpoints]
    return _discovery_response(combined_version, resources, ENDPOINT_TYPE_URL)


@app.get("/route-table")
async def route_table():
    return current_state().route_table


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


@app.get("/debug/resources")
async def debug_resources():
    state = current_state()
    return {
        "cds_version": state.cds_version,
        "clusters": state.clusters,
        "endpoints": state.endpoints,
        "route_table": state.route_table,
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=XDS_PORT)

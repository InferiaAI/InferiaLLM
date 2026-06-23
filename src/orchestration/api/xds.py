"""xDS control plane — Envoy discovery service for inference routing.

Provides Cluster (CDS) discovery responses so the inference gateway (envoyproxy)
knows about available worker nodes. Uses STRICT_DNS clusters with embedded
load_assignment, so all endpoint data is included in CDS.

Subscribes to ``xds:node:state_changed`` Redis events for near-instant
updates, with a periodic DB refresh as fallback. The primary data source
is ``InventoryRepository.list_xds_nodes()`` which returns nodes from
the ``compute_inventory`` table.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import uuid
from typing import Any
from urllib.parse import urlparse

from fastapi import APIRouter, FastAPI, Request, Response

from common.cluster_naming import build_envoy_cluster_name

logger = logging.getLogger(__name__)
router = APIRouter(tags=["xDS Control Plane"])

CLUSTER_TYPE_URL = "type.googleapis.com/envoy.config.cluster.v3.Cluster"
ENDPOINT_TYPE_URL = "type.googleapis.com/envoy.config.endpoint.v3.ClusterLoadAssignment"

XDS_CLUSTER_NAME = os.environ.get("XDS_CLUSTER_NAME", "xds_cluster")
REFRESH_DELAY = os.environ.get("XDS_REFRESH_DELAY", "5s")


# ---------------------------------------------------------------------------
# DI — follows the same configure() pattern as api/nodes.py, api/workers.py.
# ---------------------------------------------------------------------------


class _Deps:
    inventory_repo: Any = None
    event_bus: Any = None


_deps = _Deps()


def configure(*, inventory_repo, event_bus=None) -> None:
    _deps.inventory_repo = inventory_repo
    _deps.event_bus = event_bus


def _parse_advertise_url(advertise_url: str) -> tuple[str, int]:
    if "//" in advertise_url:
        parsed = urlparse(advertise_url)
        return parsed.hostname or "localhost", parsed.port or 8080
    host, _, port = advertise_url.partition(":")
    return host, int(port or 8080)


def _hash(obj) -> str:
    blob = json.dumps(obj, sort_keys=True, default=str).encode()
    return hashlib.sha1(blob).hexdigest()[:12]


DIFFUSION_ENGINES = frozenset({"inferia-diffusion"})


def _health_checks(engine: str | None) -> list[dict]:
    is_diffusion = engine in DIFFUSION_ENGINES
    return [
        {
            "timeout": "60s" if is_diffusion else "10s",
            "interval": "60s" if is_diffusion else "5s",
            "unhealthy_threshold": 30 if is_diffusion else 2,
            "healthy_threshold": 1,
            "http_health_check": {
                "path": "/healthz",
            },
        },
    ]


def _build_cluster(cluster_name: str, members: list[dict], engine: str | None = None) -> dict:
    return {
        "@type": CLUSTER_TYPE_URL,
        "name": cluster_name,
        "connect_timeout": "120s" if (engine or "") in DIFFUSION_ENGINES else "45s",
        "lb_policy": "ROUND_ROBIN",
        "type": "STRICT_DNS",
        "health_checks": _health_checks(engine),
        "load_assignment": {
            "cluster_name": cluster_name,
            "endpoints": [
                {
                    "lb_endpoints": [
                        {
                            "endpoint": {
                                "address": {
                                    "socket_address": {
                                        "address": m["host"],
                                        "port_value": m["port"],
                                    }
                                }
                            }
                        }
                        for m in members
                    ]
                }
            ],
        },
    }


async def _load_nodes() -> list[dict]:
    """Fetch all active nodes from the inventory repo.

    Returns entries with keys: id, advertise_url, pool_id, engine, healthy.
    """
    if _deps.inventory_repo is None:
        return []
    return await _deps.inventory_repo.list_xds_nodes()


async def load_ready_nodes() -> list[dict]:
    """Fetch ready nodes with active deployments directly from DB.

    Delegates to InventoryRepository.get_ready_nodes() for a filtered
    view: only 'ready' state nodes with a recent heartbeat that have
    at least one RUNNING/DEPLOYING deployment bound.

    Returns entries with keys: id, advertise_url, expose_url, pool_id,
    engine, model, endpoint, healthy, last_heartbeat.
    """
    if _deps.inventory_repo is None:
        return []
    return await _deps.inventory_repo.get_ready_nodes()


async def build_resources() -> dict:
    """Build all xDS resources (clusters) from current inventory.

    Uses STRICT_DNS clusters with embedded load_assignment, so all endpoint
    data is included in CDS. No separate EDS discovery is needed.
    """
    nodes = await load_ready_nodes()

    clusters: dict[str, dict] = {}
    route_table: dict[str, str] = {}

    pools: dict[str, list[dict]] = {}
    singletons: list[dict] = []
    for n in nodes:
        url = n.get("expose_url") or n.get("advertise_url")
        if not url:
            continue
        host, port = _parse_advertise_url(url)
        entry = {
            "host": host,
            "port": port,
            "id": n["id"],
            "engine": n.get("engine"),
            "model": n.get("model", "__default__"),
        }
        if n.get("pool_id"):
            pools.setdefault(n["pool_id"], []).append(entry)
        else:
            singletons.append(entry)

    for pool_id, members in pools.items():
        groups: set[tuple[str | None, str]] = {
            (m["engine"], m["model"]) for m in members
        }
        for engine, model in groups:
            group_members = [
                m for m in members if m["engine"] == engine and m["model"] == model
            ]
            # Use shared cluster naming logic to ensure consistency with worker_routing.py
            cluster_name = build_envoy_cluster_name(
                pool_id=pool_id, engine=engine, model=model
            )
            clusters[cluster_name] = _build_cluster(cluster_name, group_members, engine=engine)
            for m in group_members:
                route_table[m["id"]] = cluster_name

    if singletons:
        singleton_groups: dict[tuple[str | None, str], list[dict]] = {}
        for m in singletons:
            key = (m["engine"], m["model"])
            singleton_groups.setdefault(key, []).append(m)
        for (engine, model), members in singleton_groups.items():
            # Use shared cluster naming logic for singletons too
            cluster_name = build_envoy_cluster_name(
                pool_id=None, engine=engine, model=model
            )
            clusters[cluster_name] = _build_cluster(cluster_name, members, engine=engine)
            for m in members:
                route_table[m["id"]] = cluster_name

    return {
        "clusters": clusters,
        "route_table": route_table,
        "cds_version": _hash([clusters[k] for k in sorted(clusters.keys())]),
    }


def _discovery_response(version: str, resources: list, type_url: str) -> dict:
    return {
        "version_info": version,
        "resources": resources,
        "type_url": type_url,
        "nonce": uuid.uuid4().hex,
    }


@router.post("/v3/discovery:clusters")
async def discovery_clusters(request: Request):
    body = await request.json() or {}
    client_version = body.get("version_info", "")
    resources = await build_resources()
    if client_version == resources["cds_version"]:
        return Response(status_code=304)
    return _discovery_response(
        resources["cds_version"],
        list(resources["clusters"].values()),
        CLUSTER_TYPE_URL,
    )


@router.post("/v3/discovery:endpoints")
async def discovery_endpoints(request: Request):
    """Endpoint discovery (EDS) is deprecated in favor of STRICT_DNS clusters.

    All endpoint information is now embedded in CDS via load_assignment.
    This handler returns an empty response for compatibility.
    """
    return _discovery_response(
        version="1",
        resources=[],
        type_url=ENDPOINT_TYPE_URL,
    )


@router.get("/v3/route-table")
async def route_table():
    resources = await build_resources()
    return resources["route_table"]


@router.get("/v3/healthz")
async def healthz():
    return {"status": "ok"}


@router.get("/v3/debug/resources")
async def debug_resources():
    return await build_resources()


async def reconcile_on_startup() -> None:
    """Force one eager DB pull on process start so the in-process world
    (and any caches/logs) reflect already-running deployments immediately,
    rather than waiting for Envoy's first CDS poll to trigger discovery.
    """
    try:
        resources = await build_resources()
        logger.info(
            "xDS startup reconciliation: loaded %d cluster(s) from existing inventory",
            len(resources["clusters"]),
        )
    except Exception:
        logger.exception("xDS startup reconciliation failed")


async def start_xds_event_subscription(app: FastAPI) -> None:
    """Subscribe to ``xds:node:state_changed`` events on the Redis event bus.

    When a node transitions to ``ready`` or ``terminated`` the event
    payload carries ``{node_id, state, advertise_url, pool_id, engine}``
    and the xds cache is invalidated so the next discovery response picks
    up the change.  The subscription is best-effort — the periodic DB
    refresh on each discovery request is the authoritative fallback.
    """
    if _deps.event_bus is None:
        logger.info("xDS event subscription skipped: no event bus configured")
        return

    logger.info("xDS event subscription started")

    while True:
        try:
            async for _msg_id, _event in _deps.event_bus.consume(
                stream="xds:node:state_changed",
                group="xds-consumers",
                consumer="xds-1",
            ):
                logger.debug("xDS event received: %s", _event.get("state"))
                # No explicit cache to invalidate — build_resources queries the
                # DB fresh each time.  The subscription exists so the consumer
                # group anchors progress; the DB is the source of truth.
        except Exception:
            logger.exception("xDS event subscription crashed, retrying in 5s")
            await asyncio.sleep(5)

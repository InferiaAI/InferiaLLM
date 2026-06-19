from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch

from orchestration.api.xds import (
    _safe,
    _parse_advertise_url,
    _build_cluster,
    build_resources,
    configure,
)


def test_safe_sanitization():
    assert _safe("pool-123") == "pool-123"
    assert _safe("pool 123") == "pool-123"
    assert _safe("pool@123!") == "pool-123-"
    assert _safe("vllm_engine") == "vllm_engine"


def test_parse_advertise_url():
    assert _parse_advertise_url("http://host:8080") == ("host", 8080)
    assert _parse_advertise_url("host:8080") == ("host", 8080)
    assert _parse_advertise_url("host") == ("host", 8080)
    assert _parse_advertise_url("http://host") == ("host", 8080)


def test_build_cluster_format():
    members = [{"host": "h1", "port": 8080}]
    cluster = _build_cluster("test-cluster", members)
    assert cluster["name"] == "test-cluster"
    assert cluster["type"] == "STRICT_DNS"
    assert cluster["@type"] == "type.googleapis.com/envoy.config.cluster.v3.Cluster"
    assert "load_assignment" in cluster
    assert cluster["load_assignment"]["cluster_name"] == "test-cluster"





@pytest.mark.asyncio
async def test_build_resources_grouping():
    fake_nodes = [
        {"id": "n1", "advertise_url": "h1:8080", "pool_id": "p1", "engine": "vllm", "model": "gemma", "healthy": True},
        {"id": "n2", "advertise_url": "h2:8080", "pool_id": "p1", "engine": "vllm", "model": "gemma", "healthy": True},
        {"id": "n3", "advertise_url": "h3:8080", "pool_id": "p1", "engine": "ollama", "model": "llama", "healthy": True},
        {"id": "n4", "advertise_url": "h4:8080", "pool_id": None, "engine": "vllm", "model": "gemma", "healthy": True},
        {"id": "n5", "advertise_url": "h5:8080", "pool_id": "p1", "engine": "vllm", "model": "gemma", "healthy": False},
        {"id": "n6", "advertise_url": "h6:8080", "pool_id": "p1", "engine": "vllm", "model": "__default__", "healthy": True},
        {"id": "n7", "advertise_url": "h7:8080", "pool_id": None, "engine": "vllm", "model": "__default__", "healthy": True},
    ]

    mock_repo = AsyncMock()
    mock_repo.list_xds_nodes.return_value = fake_nodes
    configure(inventory_repo=mock_repo)

    rs = await build_resources()

    # Every node appears in route_table, pooled nodes map to their cluster
    assert rs["route_table"]["n1"] == "grp-p1-vllm-gemma"

    # n4 is a singleton with a known model → gets its own model-specific cluster
    assert rs["route_table"]["n4"] == "grp-vllm-gemma"
    assert "grp-vllm-gemma" in rs["clusters"]

    # n5 is unhealthy — list_xds_nodes still returns it; xDS doesn't filter
    assert "grp-p1-vllm-gemma" in rs["clusters"]
    # n5 sits in the same pool+engine+model group as n1/n2
    # Endpoint data is now embedded in cluster load_assignment
    lb = rs["clusters"]["grp-p1-vllm-gemma"]["load_assignment"]["endpoints"][0]["lb_endpoints"]
    hosts_in = {e["endpoint"]["address"]["socket_address"]["address"] for e in lb}
    assert "h5" in hosts_in  # unhealthy nodes are NOT filtered by xDS

    # n6 has model=__default__ → old-style cluster name (no model suffix)
    assert "grp-p1-vllm" in rs["clusters"]
    assert rs["route_table"]["n6"] == "grp-p1-vllm"

    # n7 has model=__default__ with no pool → falls back to inferia-workers
    assert "inferia-workers" in rs["clusters"]
    assert rs["route_table"]["n7"] == "inferia-workers"

    # Check structure
    assert len(rs["clusters"]) > 0
    assert len(rs["route_table"]) > 0
    assert rs["cds_version"]

    # Verify debug endpoint contains route_table
    from orchestration.api.xds import debug_resources
    debug = await debug_resources()
    assert "route_table" in debug

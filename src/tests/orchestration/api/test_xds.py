from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch

from orchestration.api.xds import (
    _safe,
    _parse_advertise_url,
    _build_cluster,
    _build_endpoints,
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
    cluster = _build_cluster("test-cluster")
    assert cluster["name"] == "test-cluster"
    assert cluster["type"] == "STRICT_DNS"
    assert cluster["@type"] == "type.googleapis.com/envoy.config.cluster.v3.Cluster"


def test_build_endpoints_format():
    members = [{"host": "h1", "port": 8080}]
    endpoints = _build_endpoints("test-cluster", members)
    assert endpoints["cluster_name"] == "test-cluster"
    addr = endpoints["endpoints"][0]["lb_endpoints"][0]["endpoint"]["address"]["socket_address"]
    assert addr["address"] == "h1"
    assert addr["port_value"] == 8080


@pytest.mark.asyncio
async def test_build_resources_grouping():
    fake_nodes = [
        {"id": "n1", "advertise_url": "h1:8080", "pool_id": "p1", "engine": "vllm", "healthy": True},
        {"id": "n2", "advertise_url": "h2:8080", "pool_id": "p1", "engine": "vllm", "healthy": True},
        {"id": "n3", "advertise_url": "h3:8080", "pool_id": "p1", "engine": "ollama", "healthy": True},
        {"id": "n4", "advertise_url": "h4:8080", "pool_id": None, "engine": "vllm", "healthy": True},
        {"id": "n5", "advertise_url": "h5:8080", "pool_id": "p1", "engine": "vllm", "healthy": False},
    ]

    mock_repo = AsyncMock()
    mock_repo.list_xds_nodes.return_value = fake_nodes
    configure(inventory_repo=mock_repo)

    rs = await build_resources()

    # n1 through n3 should appear in clusters but may use engine-tagged names
    assert "n1" not in rs["route_table"]  # n1 is in a pool cluster, not singleton
    assert rs["route_table"]["n1"] is not None

    # n4 is a singleton
    assert "n4" in rs["clusters"]
    assert rs["route_table"]["n4"] == "n4"

    # n5 is unhealthy (healthy=False), list_xds_nodes still returns it but
    # the xds layer doesn't filter by healthy — the DB query does that
    assert "grp-p1-vllm" in rs["clusters"]

    # Check structure
    assert len(rs["clusters"]) > 0
    assert len(rs["endpoints"]) > 0
    assert len(rs["route_table"]) > 0
    assert rs["cds_version"]

from __future__ import annotations

import pytest
from xds_control_plane.xds_shim import (
    Node,
    build_resources,
    _safe,
    _parse_advertise_url,
    build_cluster,
    build_endpoints,
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
    members = [Node(node_id="n1", host="h1", port=8080)]
    cluster = build_cluster("test-cluster", members)
    assert cluster["name"] == "test-cluster"
    assert cluster["type"] == "EDS"
    assert cluster["@type"] == "type.googleapis.com/envoy.config.cluster.v3.Cluster"

def test_build_endpoints_format():
    members = [Node(node_id="n1", host="h1", port=8080)]
    endpoints = build_endpoints("test-cluster", members)
    assert endpoints["cluster_name"] == "test-cluster"
    assert endpoints["endpoints"][0]["lb_endpoints"][0]["endpoint"]["address"]["socket_address"]["address"] == "h1"
    assert endpoints["endpoints"][0]["lb_endpoints"][0]["endpoint"]["address"]["socket_address"]["port_value"] == 8080

def test_build_resources_grouping():
    nodes = [
        Node(node_id="n1", host="h1", port=8080, pool_id="p1", engine="vllm", healthy=True),
        Node(node_id="n2", host="h2", port=8080, pool_id="p1", engine="vllm", healthy=True),
        Node(node_id="n3", host="h3", port=8080, pool_id="p1", engine="ollama", healthy=True),
        Node(node_id="n4", host="h4", port=8080, pool_id=None, engine="vllm", healthy=True),
        Node(node_id="n5", host="h5", port=8080, pool_id="p1", engine="vllm", healthy=False),
    ]
    
    rs = build_resources(nodes)
    
    # p1-vllm cluster should have n1 and n2
    assert "grp-p1-vllm" in rs.clusters
    assert "grp-p1-vllm" in rs.endpoints
    assert rs.route_table["n1"] == "grp-p1-vllm"
    assert rs.route_table["n2"] == "grp-p1-vllm"
    
    # p1-ollama cluster should have n3
    assert "grp-p1-ollama" in rs.clusters
    assert rs.route_table["n3"] == "grp-p1-ollama"
    
    # n4 is a singleton
    assert "n4" in rs.clusters
    assert rs.route_table["n4"] == "n4"
    
    # n5 is unhealthy, should be ignored
    assert "n5" not in rs.route_table

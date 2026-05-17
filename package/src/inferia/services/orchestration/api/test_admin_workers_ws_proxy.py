"""Tests for the worker logs / shell WS proxy helpers.

The proxies themselves are end-to-end WebSocket chains (gateway →
orchestration → worker) that integration tests cover during dev. What
needs lockdown in CI is the per-request URL/token derivation in
``_resolve_worker_ws_base`` — every regression in that function turned
into a "WebSocket connect failed" toast in the dashboard with no
useful detail. These tests pin its behaviour against a stub inventory.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

import pytest
from fastapi import HTTPException

from inferia.services.orchestration.api import admin_workers


NODE_ID = "11111111-2222-3333-4444-555555555555"
POOL_ID = "00000000-0000-0000-0000-000000000001"
INFERENCE_TOKEN = "test-inference-token"


class FakeInventory:
    """Returns ``node`` for every get_node_by_id call, or None to simulate 404."""

    def __init__(self, node: dict | None):
        self.node = node

    async def get_node_by_id(self, node_id: str):
        return self.node


class FakePoolRepo:
    def __init__(self, token: str = INFERENCE_TOKEN):
        self.token = token
        self.calls: list[str] = []

    async def get_or_generate_inference_token(self, *, pool_id: str) -> str:
        self.calls.append(pool_id)
        return self.token


@pytest.fixture(autouse=True)
def reset_deps():
    """Snapshot and restore the module-level deps singleton around each test."""
    saved = (
        admin_workers._deps.inventory_repo,
        admin_workers._deps.pool_repo,
    )
    yield
    admin_workers._deps.inventory_repo = saved[0]
    admin_workers._deps.pool_repo = saved[1]


def configure(node: dict | None, pool_repo=None):
    """Wire just enough of admin_workers._deps to exercise the helper."""
    admin_workers._deps.inventory_repo = FakeInventory(node)
    admin_workers._deps.pool_repo = pool_repo or FakePoolRepo()


def run(coro):
    """Synchronous wrapper around an async helper — keeps the test bodies flat."""
    return asyncio.get_event_loop().run_until_complete(coro) \
        if False else asyncio.run(coro)


def test_missing_node_raises_404():
    configure(node=None)
    with pytest.raises(HTTPException) as ei:
        run(admin_workers._resolve_worker_ws_base(NODE_ID))
    assert ei.value.status_code == 404


def test_non_worker_node_rejected():
    configure(node={
        "id": NODE_ID,
        "agent_kind": "unknown",
        "advertise_url": "http://localhost:8080",
        "pool_id": POOL_ID,
    })
    with pytest.raises(HTTPException) as ei:
        run(admin_workers._resolve_worker_ws_base(NODE_ID))
    assert ei.value.status_code == 400


def test_missing_advertise_url_returns_409():
    """No URL means the worker can't be reached — operator gets a clear hint
    rather than a TCP-connect failure inside the WS handler."""
    configure(node={
        "id": NODE_ID,
        "agent_kind": "worker",
        "advertise_url": "",
        "pool_id": POOL_ID,
    })
    with pytest.raises(HTTPException) as ei:
        run(admin_workers._resolve_worker_ws_base(NODE_ID))
    assert ei.value.status_code == 409


def test_localhost_advertise_rewrites_to_compose_default(monkeypatch):
    """Same-host dev shows ``http://localhost:8080`` in advertise_url; from
    inside the orchestration container that loopback is the orchestration
    itself. The proxy must rewrite to the worker compose service name so
    the WS handler actually reaches the worker."""
    monkeypatch.delenv("WORKER_LOCAL_FALLBACK_HOST", raising=False)
    configure(node={
        "id": NODE_ID,
        "agent_kind": "worker",
        "advertise_url": "http://localhost:8080",
        "pool_id": POOL_ID,
    })
    ws_base, token = run(admin_workers._resolve_worker_ws_base(NODE_ID))
    assert ws_base == "ws://inferia-worker:8080"
    assert token == INFERENCE_TOKEN


def test_loopback_127_001_also_rewrites(monkeypatch):
    monkeypatch.delenv("WORKER_LOCAL_FALLBACK_HOST", raising=False)
    configure(node={
        "id": NODE_ID,
        "agent_kind": "worker",
        "advertise_url": "http://127.0.0.1:18080",
        "pool_id": POOL_ID,
    })
    ws_base, _ = run(admin_workers._resolve_worker_ws_base(NODE_ID))
    # Port preserved, host rewritten.
    assert ws_base == "ws://inferia-worker:18080"


def test_fallback_host_is_overridable(monkeypatch):
    """Operators can point the proxy at a different worker hostname
    (multi-host setup, custom compose project name, etc.)."""
    monkeypatch.setenv("WORKER_LOCAL_FALLBACK_HOST", "gpu-host-7")
    configure(node={
        "id": NODE_ID,
        "agent_kind": "worker",
        "advertise_url": "http://localhost:8080",
        "pool_id": POOL_ID,
    })
    ws_base, _ = run(admin_workers._resolve_worker_ws_base(NODE_ID))
    assert ws_base == "ws://gpu-host-7:8080"


def test_routable_advertise_url_passes_through(monkeypatch):
    """In production the advertise_url is already routable; the helper must
    not mangle it."""
    monkeypatch.delenv("WORKER_LOCAL_FALLBACK_HOST", raising=False)
    configure(node={
        "id": NODE_ID,
        "agent_kind": "worker",
        "advertise_url": "https://gpu-08.fleet.example.com:8443",
        "pool_id": POOL_ID,
    })
    ws_base, _ = run(admin_workers._resolve_worker_ws_base(NODE_ID))
    # https → wss (preserve TLS), host + port carried through verbatim.
    assert ws_base == "wss://gpu-08.fleet.example.com:8443"


def test_inference_token_is_pool_scoped():
    """The token returned must come from the worker's pool, not some global
    secret — the worker's bearer middleware checks it against the
    pool-stored value."""
    pool_repo = FakePoolRepo(token="pool-specific-token")
    configure(
        node={
            "id": NODE_ID,
            "agent_kind": "worker",
            "advertise_url": "http://localhost:8080",
            "pool_id": POOL_ID,
        },
        pool_repo=pool_repo,
    )
    _, token = run(admin_workers._resolve_worker_ws_base(NODE_ID))
    assert token == "pool-specific-token"
    assert pool_repo.calls == [POOL_ID], "should look up token by the node's pool_id"


def test_missing_pool_id_is_500():
    """Worker rows without pool_id are a backend bug; the proxy should
    surface that as 500, not silently fall back to an empty token."""
    configure(node={
        "id": NODE_ID,
        "agent_kind": "worker",
        "advertise_url": "http://localhost:8080",
        "pool_id": None,
    })
    with pytest.raises(HTTPException) as ei:
        run(admin_workers._resolve_worker_ws_base(NODE_ID))
    assert ei.value.status_code == 500

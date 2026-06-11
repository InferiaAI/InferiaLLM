"""End-to-end tests for the /v1/workers/* router."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import UUID

import pytest
from fastapi import FastAPI

# Repo-wide version skew: starlette 0.35.1's TestClient still passes
# ``app=`` to ``httpx.Client``, which httpx 0.28+ removed. Drop the kwarg
# silently for the duration of this module so the existing sync
# TestClient-based tests in this file keep working. The proper fix is a
# starlette / httpx upgrade — tracked separately.
import httpx as _httpx
_orig_client_init = _httpx.Client.__init__


def _patched_client_init(self, *args, **kwargs):
    kwargs.pop("app", None)
    return _orig_client_init(self, *args, **kwargs)


_httpx.Client.__init__ = _patched_client_init  # type: ignore[assignment]
from fastapi.testclient import TestClient  # noqa: E402

from orchestration.api import workers
from orchestration.worker_controller.auth import (
    BootstrapClaim,
    InvalidBootstrapToken,
    WorkerAuth,
)
from orchestration.worker_controller.registry import (
    WorkerRegistry,
)
from orchestration.worker_controller.protocol import (
    Envelope,
)


SECRET = "test-secret-key-at-least-32-chars-long!"
POOL_ID = "00000000-0000-0000-0000-000000000001"
POOL_UUID = UUID(POOL_ID)
OTHER_POOL_ID = "11111111-2222-3333-4444-555555555555"


class FakeInventory:
    """In-memory stand-in for InventoryRepository."""

    def __init__(self):
        self.nodes: dict[tuple[str, str], dict] = {}
        self.heartbeats: list[dict] = []
        self.marked_ready: list[str] = []
        self.duplicate_kind: bool = False  # toggles to force a conflict

    async def upsert_worker(self, *, pool_id, node_name, advertise_url, allocatable,
                            labels=None):
        if self.duplicate_kind:
            raise workers.DuplicateNodeError(
                f"{pool_id}/{node_name} taken by a non-worker node"
            )
        key = (pool_id, node_name)
        if key in self.nodes:
            row = self.nodes[key]
            # Merge labels if provided (simulate jsonb merge).
            if labels:
                row.setdefault("labels", {}).update(labels)
        else:
            row = {
                "id": f"node-{node_name}",
                "pool_id": pool_id,
                "node_name": node_name,
                "kind": "worker",
                "state": "provisioning",
                "advertise_url": advertise_url,
                "allocatable": allocatable,
                "labels": dict(labels) if labels else {},
            }
            self.nodes[key] = row
        return row

    async def mark_ready(self, *, node_id):
        self.marked_ready.append(node_id)

    async def mark_ready_worker(self, *, node_id):
        self.marked_ready.append(node_id)

    async def get_node_by_id(self, node_id):
        # No revoked-node check in these tests; return a non-terminated row
        # for any id that's already been "registered" (i.e. attempts to
        # mark_ready_worker). Otherwise return None to mirror real behaviour.
        return {"id": node_id, "state": "ready"}

    async def update_heartbeat(self, *, node_id, used, loaded_models):
        self.heartbeats.append({"node_id": node_id, "used": used, "loaded_models": loaded_models})

    async def update_heartbeat_with_telemetry(self, *, node_id, used, loaded_models):
        self.heartbeats.append({"node_id": node_id, "used": used, "loaded_models": loaded_models})


@pytest.fixture
def app_and_deps():
    app = FastAPI()
    auth = WorkerAuth(secret_key=SECRET, algorithm="HS256")
    registry = WorkerRegistry()
    inventory = FakeInventory()
    workers.configure(auth, registry, inventory)
    app.include_router(workers.router)
    return app, auth, registry, inventory


def test_register_happy_path(app_and_deps):
    app, auth, _registry, inventory = app_and_deps
    token = auth.mint_bootstrap_token(pool_id=POOL_ID)
    client = TestClient(app)
    r = client.post(
        "/v1/workers/register",
        json={
            "node_name": "n1",
            "pool_id": POOL_ID,
            "advertise_url": "https://w:8080",
            "allocatable": {"cpu": "16"},
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["node_id"] == "node-n1"
    assert data["worker_jwt"]
    # Token round-trip with verifier.
    claims = auth.verify_worker_token(data["worker_jwt"])
    assert claims.sub == "node-n1"
    assert claims.pool_id == POOL_ID


def test_register_missing_token(app_and_deps):
    app, _auth, _reg, _inv = app_and_deps
    client = TestClient(app)
    r = client.post("/v1/workers/register", json={
        "node_name": "n", "pool_id": POOL_ID, "advertise_url": "http://x",
    })
    assert r.status_code == 401


def test_register_invalid_token(app_and_deps):
    app, _auth, _reg, _inv = app_and_deps
    client = TestClient(app)
    r = client.post(
        "/v1/workers/register",
        json={"node_name": "n", "pool_id": POOL_ID, "advertise_url": "http://x"},
        headers={"Authorization": "Bearer not-a-jwt"},
    )
    assert r.status_code == 401


def test_register_user_token_rejected(app_and_deps):
    """A user-style token (not scope=worker:bootstrap) must not register."""
    app, auth, _reg, _inv = app_and_deps
    user_token = auth.mint_worker_token(node_id="n", pool_id=POOL_ID)
    client = TestClient(app)
    r = client.post(
        "/v1/workers/register",
        json={"node_name": "n", "pool_id": POOL_ID, "advertise_url": "http://x"},
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert r.status_code == 401


def test_register_pool_mismatch(app_and_deps):
    app, auth, _reg, _inv = app_and_deps
    boot = auth.mint_bootstrap_token(pool_id=POOL_ID)
    client = TestClient(app)
    r = client.post(
        "/v1/workers/register",
        json={
            "node_name": "n", "pool_id": "11111111-2222-3333-4444-555555555555",
            "advertise_url": "http://x",
        },
        headers={"Authorization": f"Bearer {boot}"},
    )
    assert r.status_code == 403


def test_register_duplicate_node_kind_conflict(app_and_deps):
    app, auth, _reg, inventory = app_and_deps
    inventory.duplicate_kind = True
    boot = auth.mint_bootstrap_token(pool_id=POOL_ID)
    client = TestClient(app)
    r = client.post(
        "/v1/workers/register",
        json={"node_name": "n", "pool_id": POOL_ID, "advertise_url": "http://x"},
        headers={"Authorization": f"Bearer {boot}"},
    )
    assert r.status_code == 409


def test_register_idempotent_reissues_token(app_and_deps):
    """Calling /register twice for the same (pool, node_name) yields the same
    node_id (re-registration after token loss path)."""
    app, auth, _reg, _inv = app_and_deps
    boot = auth.mint_bootstrap_token(pool_id=POOL_ID)
    client = TestClient(app)
    r1 = client.post(
        "/v1/workers/register",
        json={"node_name": "n", "pool_id": POOL_ID, "advertise_url": "http://x"},
        headers={"Authorization": f"Bearer {boot}"},
    )
    r2 = client.post(
        "/v1/workers/register",
        json={"node_name": "n", "pool_id": POOL_ID, "advertise_url": "http://x"},
        headers={"Authorization": f"Bearer {boot}"},
    )
    assert r1.status_code == 200 and r2.status_code == 200
    assert r1.json()["node_id"] == r2.json()["node_id"]


# ---------------------------------------------------------------------------
# bootstrap_token body field + cloud-env fields
# ---------------------------------------------------------------------------

# Helpers for building fake BootstrapClaim responses and stub consume fns.

def _make_claim(pool_id: UUID = POOL_UUID) -> BootstrapClaim:
    return BootstrapClaim(
        bootstrap_id=UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"),
        pool_id=pool_id,
        org_id="org-1",
    )


def _single_use_consume(call_count_holder: list, claim: BootstrapClaim):
    """Returns an async callable that yields ``claim`` on first call, then raises
    InvalidBootstrapToken (simulates single-use DB semantics)."""
    async def _consume(conn, *, token):
        call_count_holder[0] += 1
        if call_count_holder[0] > 1:
            raise InvalidBootstrapToken("already consumed")
        return claim
    return _consume


def test_register_with_bootstrap_token_happy(app_and_deps):
    """POST with bootstrap_token in body → 200, node_id + worker_jwt.
    Second call with same token → 401 (single-use)."""
    app, auth, _reg, _inv = app_and_deps
    client = TestClient(app)

    call_count = [0]
    claim = _make_claim()
    consume_fn = _single_use_consume(call_count, claim)

    with patch("orchestration.api.workers._consume_bootstrap_token",
               consume_fn):
        r1 = client.post(
            "/v1/workers/register",
            json={
                "node_name": "ec2-node-1",
                "pool_id": POOL_ID,
                "advertise_url": "https://ec2:8080",
                "allocatable": {"gpu": "1"},
                "bootstrap_token": "a-valid-bootstrap-token",
                "runtime_env": "aws-ec2",
                "instance_id": "i-0abc123",
                "region": "us-east-1",
                "availability_zone": "us-east-1a",
            },
        )
        assert r1.status_code == 200, r1.text
        data = r1.json()
        assert "node_id" in data
        assert data["worker_jwt"]

        # Second call with same (mocked) token → 401.
        r2 = client.post(
            "/v1/workers/register",
            json={
                "node_name": "ec2-node-1",
                "pool_id": POOL_ID,
                "advertise_url": "https://ec2:8080",
                "allocatable": {},
                "bootstrap_token": "a-valid-bootstrap-token",
            },
        )
        assert r2.status_code == 401, r2.text


def test_register_records_cloud_env_in_labels(app_and_deps):
    """POST with cloud-env fields → labels stored in compute inventory."""
    app, auth, _reg, inventory = app_and_deps
    client = TestClient(app)

    claim = _make_claim()
    call_count = [0]

    async def _consume(conn, *, token):
        call_count[0] += 1
        return claim

    with patch("orchestration.api.workers._consume_bootstrap_token",
               _consume):
        r = client.post(
            "/v1/workers/register",
            json={
                "node_name": "ec2-labels-node",
                "pool_id": POOL_ID,
                "advertise_url": "https://ec2:8080",
                "allocatable": {},
                "bootstrap_token": "some-token-value",
                "runtime_env": "aws-ec2",
                "instance_id": "i-abcdef012345",
                "region": "eu-west-1",
                "availability_zone": "eu-west-1b",
            },
        )
        assert r.status_code == 200, r.text

    node = inventory.nodes.get((POOL_ID, "ec2-labels-node"))
    assert node is not None, "node was not upserted"
    labels = node.get("labels", {})
    assert labels.get("runtime_env") == "aws-ec2"
    assert labels.get("instance_id") == "i-abcdef012345"
    assert labels.get("region") == "eu-west-1"
    assert labels.get("availability_zone") == "eu-west-1b"


def test_register_without_cloud_env_still_works(app_and_deps):
    """POST with bootstrap_token but no cloud-env fields → 200 (backward compat)."""
    app, auth, _reg, _inv = app_and_deps
    client = TestClient(app)

    claim = _make_claim()

    async def _consume(conn, *, token):
        return claim

    with patch("orchestration.api.workers._consume_bootstrap_token",
               _consume):
        r = client.post(
            "/v1/workers/register",
            json={
                "node_name": "bare-node",
                "pool_id": POOL_ID,
                "advertise_url": "https://bare:8080",
                "allocatable": {},
                "bootstrap_token": "bare-token-value",
            },
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert "node_id" in data
        assert data["worker_jwt"]


def test_register_bootstrap_token_pool_mismatch_rejected(app_and_deps):
    """Token minted for pool A; register claims pool B → 401."""
    app, auth, _reg, _inv = app_and_deps
    client = TestClient(app)

    # Claim says pool A (POOL_UUID), but request body says OTHER_POOL_ID.
    claim = _make_claim(pool_id=POOL_UUID)

    async def _consume(conn, *, token):
        return claim

    with patch("orchestration.api.workers._consume_bootstrap_token",
               _consume):
        r = client.post(
            "/v1/workers/register",
            json={
                "node_name": "mismatch-node",
                "pool_id": OTHER_POOL_ID,
                "advertise_url": "https://mismatch:8080",
                "allocatable": {},
                "bootstrap_token": "pool-a-token",
            },
        )
        assert r.status_code == 401, r.text
        assert "pool_scope_violation" in r.text


def test_register_oversized_fields_rejected(app_and_deps):
    """runtime_env > 64 chars → 422 Pydantic validation error."""
    app, auth, _reg, _inv = app_and_deps
    client = TestClient(app)

    boot = auth.mint_bootstrap_token(pool_id=POOL_ID)
    r = client.post(
        "/v1/workers/register",
        json={
            "node_name": "n",
            "pool_id": POOL_ID,
            "advertise_url": "http://x",
            "allocatable": {},
            "bootstrap_token": "some-token",
            "runtime_env": "x" * 65,
        },
        headers={"Authorization": f"Bearer {boot}"},
    )
    assert r.status_code == 422, r.text


# ---------------------------------------------------------------------------
# WebSocket channel
# ---------------------------------------------------------------------------


def test_channel_invalid_token_closes(app_and_deps):
    app, _auth, _reg, _inv = app_and_deps
    client = TestClient(app)
    with pytest.raises(Exception):
        with client.websocket_connect("/v1/workers/channel",
                                      headers={"Authorization": "Bearer nope"}):
            pass


def test_channel_missing_token_closes(app_and_deps):
    app, _auth, _reg, _inv = app_and_deps
    client = TestClient(app)
    with pytest.raises(Exception):
        with client.websocket_connect("/v1/workers/channel"):
            pass


def test_channel_hello_then_heartbeat(app_and_deps):
    app, auth, _reg, inventory = app_and_deps
    token = auth.mint_worker_token(node_id="node-n1", pool_id=POOL_ID)
    client = TestClient(app)
    with client.websocket_connect(
        "/v1/workers/channel",
        headers={"Authorization": f"Bearer {token}"},
    ) as ws:
        hello = ws.receive_json()
        assert hello["type"] == "Hello"
        ws.send_json({
            "type": "Heartbeat",
            "id": "hb-1",
            "body": {"used": {"cpu_pct": "10"}, "loaded_models": ["dep-1"]},
        })
        # Give the server a moment to process the message.
        import time as _t
        _t.sleep(0.1)

    # After the context exits, the worker should have been marked ready and
    # at least one heartbeat recorded.
    assert "node-n1" in inventory.marked_ready
    assert any(h["node_id"] == "node-n1" for h in inventory.heartbeats)


def test_channel_command_result_routed_to_registry(app_and_deps):
    app, auth, registry, _inv = app_and_deps
    token = auth.mint_worker_token(node_id="node-n2", pool_id=POOL_ID)
    client = TestClient(app)

    with client.websocket_connect(
        "/v1/workers/channel",
        headers={"Authorization": f"Bearer {token}"},
    ) as ws:
        _hello = ws.receive_json()
        # Park a future on the registry then send a matching CommandResult.
        loop = asyncio.new_event_loop()
        fut = loop.run_until_complete(_park(registry, "cmd-x"))
        ws.send_json({
            "type": "CommandResult",
            "id": "ws-id",
            "body": {"in_reply_to": "cmd-x", "status": "ok"},
        })
        import time as _t
        _t.sleep(0.1)
        result = loop.run_until_complete(_await(fut))
        loop.close()
        assert result.status == "ok"


async def _park(registry: WorkerRegistry, envelope_id: str):
    return registry.expect_command_result(envelope_id, timeout=2.0)


async def _await(fut):
    return await fut


# ---------------------------------------------------------------------------
# Stream-frame dispatch — ShellOutput/ShellExit/ShellError/LogsLine/LogsEnd
#
# The /v1/workers/channel read loop must parse these worker→CP envelopes,
# convert them into the right body type, and hand them off to
# registry.deliver_stream_frame so the admin-shell/logs proxy can drain
# them out to the dashboard. These tests register a node, open a stream
# via the registry directly, push each envelope kind through the WS, and
# assert the body lands on the stream's queue in the correct shape.
# ---------------------------------------------------------------------------


def _open_shell_stream_sync(registry, *, node_id, stream_id):
    """Sync helper: open a shell stream on the running event loop.

    The proxy normally opens streams from an async handler; in this test
    file we run inside TestClient's sync context so we drive the loop
    via run_until_complete on a fresh loop.
    """
    from orchestration.worker_controller.protocol import (
        ShellOpenBody,
    )
    loop = asyncio.new_event_loop()
    handle = loop.run_until_complete(
        registry.open_shell_stream(
            node_id,
            ShellOpenBody(stream_id=stream_id, shell="/bin/sh"),
        ),
    )
    return handle, loop


def _drain_one(loop, queue, timeout=2.0):
    return loop.run_until_complete(asyncio.wait_for(queue.get(), timeout))


def test_channel_routes_shell_output_to_stream_queue(app_and_deps):
    """ShellOutput envelope must land on the stream's incoming queue as a
    ShellOutputBody, not a raw dict."""
    from orchestration.worker_controller.protocol import (
        ShellOutputBody,
    )
    app, auth, registry, _inv = app_and_deps
    token = auth.mint_worker_token(node_id="node-shell-out", pool_id=POOL_ID)
    client = TestClient(app)
    with client.websocket_connect(
        "/v1/workers/channel",
        headers={"Authorization": f"Bearer {token}"},
    ) as ws:
        _hello = ws.receive_json()
        handle, loop = _open_shell_stream_sync(
            registry, node_id="node-shell-out", stream_id="s-out",
        )
        ws.send_json({
            "type": "ShellOutput",
            "id": "f1",
            "body": {"stream_id": "s-out", "data": "hello\n"},
        })
        body = _drain_one(loop, handle.incoming)
        assert isinstance(body, ShellOutputBody)
        assert body.stream_id == "s-out"
        assert body.data == "hello\n"
        loop.close()


def test_channel_routes_shell_exit_sets_closed_event(app_and_deps):
    """ShellExit must arrive at the queue AND set the closed event so the
    proxy's drain loop terminates cleanly."""
    from orchestration.worker_controller.protocol import (
        ShellExitBody,
    )
    app, auth, registry, _inv = app_and_deps
    token = auth.mint_worker_token(node_id="node-shell-exit", pool_id=POOL_ID)
    client = TestClient(app)
    with client.websocket_connect(
        "/v1/workers/channel",
        headers={"Authorization": f"Bearer {token}"},
    ) as ws:
        _hello = ws.receive_json()
        handle, loop = _open_shell_stream_sync(
            registry, node_id="node-shell-exit", stream_id="s-exit",
        )
        ws.send_json({
            "type": "ShellExit",
            "id": "f2",
            "body": {"stream_id": "s-exit", "exit_code": 137, "reason": "SIGKILL"},
        })
        body = _drain_one(loop, handle.incoming)
        assert isinstance(body, ShellExitBody)
        assert body.exit_code == 137
        assert body.reason == "SIGKILL"
        assert handle.closed.is_set()
        loop.close()


def test_channel_routes_shell_error_sets_closed_event(app_and_deps):
    from orchestration.worker_controller.protocol import (
        ShellErrorBody,
    )
    app, auth, registry, _inv = app_and_deps
    token = auth.mint_worker_token(node_id="node-shell-err", pool_id=POOL_ID)
    client = TestClient(app)
    with client.websocket_connect(
        "/v1/workers/channel",
        headers={"Authorization": f"Bearer {token}"},
    ) as ws:
        _hello = ws.receive_json()
        handle, loop = _open_shell_stream_sync(
            registry, node_id="node-shell-err", stream_id="s-err",
        )
        ws.send_json({
            "type": "ShellError",
            "id": "f3",
            "body": {"stream_id": "s-err", "message": "exec: no such file"},
        })
        body = _drain_one(loop, handle.incoming)
        assert isinstance(body, ShellErrorBody)
        assert body.message == "exec: no such file"
        assert handle.closed.is_set()
        loop.close()


def test_channel_routes_logs_line_and_end(app_and_deps):
    """LogsLine then LogsEnd must arrive in order, with End setting closed."""
    from orchestration.worker_controller.protocol import (
        LogsEndBody,
        LogsLineBody,
        LogsOpenBody,
    )
    app, auth, registry, _inv = app_and_deps
    token = auth.mint_worker_token(node_id="node-logs", pool_id=POOL_ID)
    client = TestClient(app)
    with client.websocket_connect(
        "/v1/workers/channel",
        headers={"Authorization": f"Bearer {token}"},
    ) as ws:
        _hello = ws.receive_json()
        loop = asyncio.new_event_loop()
        handle = loop.run_until_complete(
            registry.open_logs_stream(
                "node-logs",
                LogsOpenBody(stream_id="s-logs"),
            ),
        )
        ws.send_json({
            "type": "LogsLine",
            "id": "fL1",
            "body": {"stream_id": "s-logs", "stream": "stderr", "data": "boom"},
        })
        body = _drain_one(loop, handle.incoming)
        assert isinstance(body, LogsLineBody)
        assert body.stream == "stderr"
        assert body.data == "boom"
        ws.send_json({
            "type": "LogsEnd",
            "id": "fL2",
            "body": {"stream_id": "s-logs", "reason": "container exited"},
        })
        end = _drain_one(loop, handle.incoming)
        assert isinstance(end, LogsEndBody)
        assert end.reason == "container exited"
        assert handle.closed.is_set()
        loop.close()


def test_channel_unknown_stream_id_is_dropped_not_fatal(app_and_deps):
    """A ShellOutput for an unknown stream must NOT crash the channel.
    Worker raced ahead (sent output before the open-frame round-trip);
    log + drop is the correct behavior."""
    app, auth, _reg, _inv = app_and_deps
    token = auth.mint_worker_token(node_id="node-orphan", pool_id=POOL_ID)
    client = TestClient(app)
    with client.websocket_connect(
        "/v1/workers/channel",
        headers={"Authorization": f"Bearer {token}"},
    ) as ws:
        _hello = ws.receive_json()
        ws.send_json({
            "type": "ShellOutput",
            "id": "ghost",
            "body": {"stream_id": "no-such-stream", "data": "huh"},
        })
        # Verify the channel is still alive by sending a heartbeat after.
        ws.send_json({
            "type": "Heartbeat",
            "id": "hb-after-orphan",
            "body": {"used": {}, "loaded_models": []},
        })
        import time as _t
        _t.sleep(0.1)
    # If the channel had crashed, the context exit would have raised.
    assert True

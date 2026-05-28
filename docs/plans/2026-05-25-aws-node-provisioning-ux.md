# AWS Compute Node Provisioning UX — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When a user creates an AWS compute node from the dashboard, eagerly provision an EC2 instance and surface step-by-step allocation/creation progress in the InstanceDetail page. Add Logs and Web Shell tabs for AWS nodes mirroring the worker-node UI.

**Architecture:** Append-only `node_provisioning_events` table fed by an injected `progress_writer` callable in `PulumiAWSAdapter._provision_async`. Pulumi `on_event` callback bridges engine events into the table. REST endpoints expose phase summary, event log (with `?after=<id>` cursor), and on-demand EC2 console output. Dashboard polls every 2s during provisioning, falls back to the existing worker WS flow once the worker registers.

**Tech Stack:** Python 3.12, FastAPI, asyncpg, Pulumi automation Python SDK, React 19 + Vite, TanStack Query, Vitest.

**Spec:** `docs/specs/2026-05-25-aws-node-provisioning-ux.md`

**Commit conventions:**
- Sign every commit with `git -c user.signingkey=/home/ankit/.ssh/id_ed25519_gh commit -S -m "<msg>"`.
- Never mention Claude/AI/Anthropic/co-author in commit messages.

**Test conventions (per user CLAUDE.md):**
- ≥95% line coverage on touched code.
- Test edge cases: empty state, cursor exhaustion, mid-stream failures, concurrent calls, timeouts.

---

## File Structure

**Backend — create:**
- `package/src/inferia/infra/schema/migrations/20260525_add_node_provisioning_events.sql` — table + index
- `package/src/inferia/services/orchestration/repositories/node_provisioning_repo.py` — repo
- `package/src/inferia/services/orchestration/repositories/tests/test_node_provisioning_repo.py`
- `package/src/inferia/services/orchestration/services/adapter_engine/adapters/pulumi/progress_writer.py` — thread-safe async writer wrapping the repo
- `package/src/inferia/services/orchestration/services/adapter_engine/adapters/pulumi/tests/test_progress_writer.py`
- `package/src/inferia/services/orchestration/services/adapter_engine/adapters/pulumi/tests/test_pulumi_aws_adapter_progress.py`
- `package/src/inferia/services/orchestration/services/model_deployment/tests/test_createpool_aws_eager.py`
- `package/src/inferia/services/orchestration/api/tests/test_nodes_provisioning_endpoints.py`

**Backend — modify:**
- `package/src/inferia/services/orchestration/services/adapter_engine/adapters/pulumi/pulumi_aws_adapter.py` — `provision_node` accepts `progress_writer`; `_provision_async` writes phases + spawns cloud-init poller; uses `on_event`.
- `package/src/inferia/services/orchestration/services/model_deployment/deployment_server.py:1086-1132` — AWS path inserts `state='provisioning'` placeholder and calls `adapter.provision_node` with a `progress_writer`.
- `package/src/inferia/services/orchestration/api/nodes.py` — three new endpoints + extend `nodes_api.configure(...)` deps.
- `package/src/inferia/services/orchestration/server.py:226-233` — wire `provisioning_repo` + AWS adapter into `nodes_api.configure`.

**Frontend — create:**
- `apps/dashboard/src/components/nodes/ProvisioningStatus.tsx`
- `apps/dashboard/src/components/nodes/ProvisioningStatus.test.tsx`
- `apps/dashboard/src/services/provisioningService.ts` — small REST wrapper
- `apps/dashboard/src/services/provisioningService.test.ts`

**Frontend — modify:**
- `apps/dashboard/src/components/nodes/NodeLogs.tsx` — branch on `state`/`provider`
- `apps/dashboard/src/components/nodes/NodeLogs.test.tsx` (create)
- `apps/dashboard/src/components/nodes/NodeShell.tsx` — disabled state during provisioning
- `apps/dashboard/src/components/nodes/NodeShell.test.tsx` (create)
- `apps/dashboard/src/pages/Compute/InstanceDetail.tsx` — adaptive poll, AWS tab visibility, mount ProvisioningStatus
- `apps/dashboard/src/pages/Compute/InstanceDetail.test.tsx` (create)
- `apps/dashboard/src/pages/Compute/NewPool.tsx` — navigate to instance detail after AWS createpool

---

## Task 1: Migration — `node_provisioning_events` table

**Files:**
- Create: `package/src/inferia/infra/schema/migrations/20260525_add_node_provisioning_events.sql`

- [ ] **Step 1: Write the migration SQL**

```sql
-- 20260525_add_node_provisioning_events.sql
-- Append-only event log for provider provisioning UX. One row per phase
-- state transition and per Pulumi/cloud-init log line. Read with the
-- cursor `WHERE pool_id=$1 AND id > $2 ORDER BY id LIMIT $3` for the
-- dashboard polling path.

CREATE TABLE IF NOT EXISTS node_provisioning_events (
    id         BIGSERIAL PRIMARY KEY,
    pool_id    UUID        NOT NULL,
    node_id    UUID,
    phase      TEXT        NOT NULL,
    status     TEXT        NOT NULL,
    message    TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_node_provisioning_events_pool_id_id
    ON node_provisioning_events (pool_id, id);
```

- [ ] **Step 2: Apply the migration locally and verify**

```bash
docker exec deploy-postgres-1 psql -U inferia -d inferia \
  -f - < /storage/intern/hooman/work/InferiaLLM/package/src/inferia/infra/schema/migrations/20260525_add_node_provisioning_events.sql
docker exec deploy-postgres-1 psql -U inferia -d inferia \
  -c "\d node_provisioning_events"
```

Expected: table description shows `id`, `pool_id`, `node_id`, `phase`, `status`, `message`, `created_at` with the index `ix_node_provisioning_events_pool_id_id` listed.

- [ ] **Step 3: Commit**

```bash
cd /storage/intern/hooman/work/InferiaLLM
git add package/src/inferia/infra/schema/migrations/20260525_add_node_provisioning_events.sql
git -c user.signingkey=/home/ankit/.ssh/id_ed25519_gh commit -S \
  -m "infra/schema: add node_provisioning_events table"
```

---

## Task 2: Repository — `NodeProvisioningRepo`

**Files:**
- Create: `package/src/inferia/services/orchestration/repositories/node_provisioning_repo.py`
- Create: `package/src/inferia/services/orchestration/repositories/tests/test_node_provisioning_repo.py`

- [ ] **Step 1: Write failing tests**

Create `package/src/inferia/services/orchestration/repositories/tests/test_node_provisioning_repo.py`:

```python
"""Tests for NodeProvisioningRepo. Uses an in-memory asyncpg fake.

The repo only needs `fetchval`, `fetch`, `fetchrow`, `execute`. Mock at
that level so the tests don't require a live postgres.
"""
from __future__ import annotations
import pytest
from uuid import uuid4
from datetime import datetime, timezone

from inferia.services.orchestration.repositories.node_provisioning_repo import (
    NodeProvisioningRepo,
    PHASES,
)


class FakeConn:
    def __init__(self):
        self.events = []  # rows: [{id, pool_id, node_id, phase, status, message, created_at}]
        self._next_id = 1

    async def fetchval(self, query, *args):
        # Only the INSERT ... RETURNING id path uses fetchval here.
        assert "INSERT INTO node_provisioning_events" in query
        row = {
            "id": self._next_id,
            "pool_id": args[0],
            "node_id": args[1],
            "phase": args[2],
            "status": args[3],
            "message": args[4],
            "created_at": datetime.now(timezone.utc),
        }
        self._next_id += 1
        self.events.append(row)
        return row["id"]

    async def fetch(self, query, *args):
        # list_events_after: SELECT ... WHERE pool_id=$1 AND id>$2 ORDER BY id LIMIT $3
        if "ORDER BY id" in query:
            pool_id, after_id, limit = args
            return [
                dict(r) for r in self.events
                if r["pool_id"] == pool_id and r["id"] > after_id
            ][:limit]
        # summarize_phases: SELECT DISTINCT ON (phase) ...
        if "DISTINCT ON" in query:
            (pool_id,) = args
            latest = {}
            firsts = {}
            for r in self.events:
                if r["pool_id"] != pool_id:
                    continue
                if r["status"] == "log":
                    continue
                latest[r["phase"]] = r
                firsts.setdefault(r["phase"], r)
            out = []
            for phase, r in latest.items():
                out.append({
                    "phase": phase,
                    "status": r["status"],
                    "started_at": firsts[phase]["created_at"],
                    "ended_at": r["created_at"] if r["status"] in ("succeeded", "failed") else None,
                    "last_message": r["message"],
                })
            return out
        raise AssertionError(f"unexpected query: {query}")


@pytest.fixture
def repo():
    return NodeProvisioningRepo(FakeConn())


@pytest.mark.asyncio
async def test_append_event_returns_monotonic_id(repo):
    pool = uuid4()
    a = await repo.append_event(pool_id=pool, phase="prepare", status="running")
    b = await repo.append_event(pool_id=pool, phase="prepare", status="succeeded")
    assert a == 1
    assert b == 2


@pytest.mark.asyncio
async def test_append_event_persists_optional_fields(repo):
    pool = uuid4()
    node = uuid4()
    await repo.append_event(
        pool_id=pool, phase="prepare", status="running",
        message="loading creds", node_id=node,
    )
    assert repo.db.events[0]["message"] == "loading creds"
    assert repo.db.events[0]["node_id"] == node


@pytest.mark.asyncio
async def test_list_events_after_cursor_advances(repo):
    pool = uuid4()
    for i in range(5):
        await repo.append_event(pool_id=pool, phase="pulumi_up", status="log",
                                message=f"line {i}")
    page1 = await repo.list_events_after(pool_id=pool, after_id=0, limit=3)
    assert len(page1) == 3
    assert [r["id"] for r in page1] == [1, 2, 3]
    last = page1[-1]["id"]
    page2 = await repo.list_events_after(pool_id=pool, after_id=last, limit=10)
    assert [r["id"] for r in page2] == [4, 5]


@pytest.mark.asyncio
async def test_list_events_after_empty_pool_returns_empty(repo):
    out = await repo.list_events_after(pool_id=uuid4(), after_id=0, limit=10)
    assert out == []


@pytest.mark.asyncio
async def test_list_events_after_limit_enforced(repo):
    pool = uuid4()
    for i in range(10):
        await repo.append_event(pool_id=pool, phase="cloud_init", status="log")
    out = await repo.list_events_after(pool_id=pool, after_id=0, limit=4)
    assert len(out) == 4


@pytest.mark.asyncio
async def test_summarize_phases_returns_latest_per_phase(repo):
    pool = uuid4()
    await repo.append_event(pool_id=pool, phase="prepare", status="running")
    await repo.append_event(pool_id=pool, phase="prepare", status="succeeded")
    await repo.append_event(pool_id=pool, phase="ami_lookup", status="running")
    summary = await repo.summarize_phases(pool_id=pool)
    by_phase = {r["phase"]: r for r in summary}
    assert by_phase["prepare"]["status"] == "succeeded"
    assert by_phase["prepare"]["ended_at"] is not None
    assert by_phase["ami_lookup"]["status"] == "running"
    assert by_phase["ami_lookup"]["ended_at"] is None


@pytest.mark.asyncio
async def test_summarize_phases_ignores_log_events(repo):
    pool = uuid4()
    await repo.append_event(pool_id=pool, phase="pulumi_up", status="running")
    for i in range(20):
        await repo.append_event(pool_id=pool, phase="pulumi_up", status="log",
                                message=f"l{i}")
    await repo.append_event(pool_id=pool, phase="pulumi_up", status="succeeded")
    summary = await repo.summarize_phases(pool_id=pool)
    assert len(summary) == 1
    assert summary[0]["status"] == "succeeded"


@pytest.mark.asyncio
async def test_summarize_phases_unknown_pool_returns_empty(repo):
    out = await repo.summarize_phases(pool_id=uuid4())
    assert out == []


@pytest.mark.asyncio
async def test_current_phase_returns_running_phase(repo):
    pool = uuid4()
    await repo.append_event(pool_id=pool, phase="prepare", status="succeeded")
    await repo.append_event(pool_id=pool, phase="pulumi_up", status="running")
    assert await repo.current_phase(pool_id=pool) == "pulumi_up"


@pytest.mark.asyncio
async def test_current_phase_returns_none_when_terminal(repo):
    pool = uuid4()
    await repo.append_event(pool_id=pool, phase="ready", status="succeeded")
    assert await repo.current_phase(pool_id=pool) is None


@pytest.mark.asyncio
async def test_phases_constant_has_eight_entries():
    assert PHASES == (
        "prepare", "ami_lookup", "pulumi_init", "pulumi_up",
        "ec2_running", "cloud_init", "worker_bootstrap", "ready",
    )
```

- [ ] **Step 2: Run tests, confirm failure**

```bash
cd /storage/intern/hooman/work/InferiaLLM
PYTHONPATH=package/src python -m pytest \
  package/src/inferia/services/orchestration/repositories/tests/test_node_provisioning_repo.py -v
```

Expected: ImportError (module does not exist yet).

- [ ] **Step 3: Implement the repo**

Create `package/src/inferia/services/orchestration/repositories/node_provisioning_repo.py`:

```python
"""Append-only event log for node provisioning UX.

One row per phase status transition plus one row per `log`-status entry
emitted by Pulumi's on_event callback and the cloud-init console poller.
The dashboard polls with a `?after=<id>` cursor; that's the only read
path other than the phase summary used by the Overview tab.
"""
from __future__ import annotations
from typing import Optional, Sequence
from uuid import UUID


PHASES: tuple[str, ...] = (
    "prepare",
    "ami_lookup",
    "pulumi_init",
    "pulumi_up",
    "ec2_running",
    "cloud_init",
    "worker_bootstrap",
    "ready",
)

_TERMINAL_STATUSES = ("succeeded", "failed")


class NodeProvisioningRepo:
    def __init__(self, db):
        self.db = db

    async def append_event(
        self,
        *,
        pool_id: UUID,
        phase: str,
        status: str,
        message: Optional[str] = None,
        node_id: Optional[UUID] = None,
    ) -> int:
        return await self.db.fetchval(
            """
            INSERT INTO node_provisioning_events
                (pool_id, node_id, phase, status, message)
            VALUES ($1, $2, $3, $4, $5)
            RETURNING id
            """,
            pool_id,
            node_id,
            phase,
            status,
            message,
        )

    async def list_events_after(
        self, *, pool_id: UUID, after_id: int, limit: int = 500,
    ) -> Sequence[dict]:
        rows = await self.db.fetch(
            """
            SELECT id, pool_id, node_id, phase, status, message, created_at
            FROM node_provisioning_events
            WHERE pool_id = $1 AND id > $2
            ORDER BY id
            LIMIT $3
            """,
            pool_id,
            after_id,
            limit,
        )
        return [dict(r) for r in rows]

    async def summarize_phases(self, *, pool_id: UUID) -> Sequence[dict]:
        """Latest non-log row per phase, with started_at = first row time.

        Returned shape per phase:
            {phase, status, started_at, ended_at, last_message}
        ended_at is set only when status in ('succeeded','failed').
        """
        rows = await self.db.fetch(
            """
            SELECT DISTINCT ON (phase)
                phase, status, message, created_at,
                (SELECT MIN(created_at)
                   FROM node_provisioning_events e2
                  WHERE e2.pool_id = $1 AND e2.phase = e1.phase
                ) AS started_at
            FROM node_provisioning_events e1
            WHERE pool_id = $1 AND status <> 'log'
            ORDER BY phase, id DESC
            """,
            pool_id,
        )
        out = []
        for r in rows:
            d = dict(r)
            ended_at = d["created_at"] if d["status"] in _TERMINAL_STATUSES else None
            out.append({
                "phase": d["phase"],
                "status": d["status"],
                "started_at": d["started_at"],
                "ended_at": ended_at,
                "last_message": d["message"],
            })
        return out

    async def current_phase(self, *, pool_id: UUID) -> Optional[str]:
        summary = await self.summarize_phases(pool_id=pool_id)
        for r in summary:
            if r["status"] == "running":
                return r["phase"]
        return None
```

- [ ] **Step 4: Run tests, confirm pass**

```bash
PYTHONPATH=package/src python -m pytest \
  package/src/inferia/services/orchestration/repositories/tests/test_node_provisioning_repo.py -v
```

Expected: 11 passed.

- [ ] **Step 5: Commit**

```bash
cd /storage/intern/hooman/work/InferiaLLM
git add package/src/inferia/services/orchestration/repositories/node_provisioning_repo.py \
        package/src/inferia/services/orchestration/repositories/tests/test_node_provisioning_repo.py
git -c user.signingkey=/home/ankit/.ssh/id_ed25519_gh commit -S \
  -m "orchestration/repos: add NodeProvisioningRepo"
```

---

## Task 3: Thread-safe `ProgressWriter`

The Pulumi `on_event` callback fires on the Pulumi thread, not the asyncio thread. We need a writer that bridges thread → async-loop safely.

**Files:**
- Create: `package/src/inferia/services/orchestration/services/adapter_engine/adapters/pulumi/progress_writer.py`
- Create: `package/src/inferia/services/orchestration/services/adapter_engine/adapters/pulumi/tests/test_progress_writer.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_progress_writer.py`:

```python
"""ProgressWriter bridges Pulumi-thread callbacks into the asyncio loop.

The writer exposes a synchronous `write(phase, status, message=None)`
method safe to call from the Pulumi thread, plus an async
`write_async(...)` method for in-loop callers (provision_node phases
before stack.up runs).
"""
from __future__ import annotations
import asyncio
import pytest
from uuid import uuid4

from inferia.services.orchestration.services.adapter_engine.adapters.pulumi.progress_writer import (
    ProgressWriter,
)


class StubRepo:
    def __init__(self):
        self.calls = []

    async def append_event(self, **kw):
        self.calls.append(kw)
        return len(self.calls)


@pytest.mark.asyncio
async def test_write_async_appends_event():
    repo = StubRepo()
    pool = uuid4()
    w = ProgressWriter(repo, pool_id=pool, node_id=None)
    await w.write_async("prepare", "running", "loading creds")
    assert repo.calls == [{
        "pool_id": pool, "node_id": None,
        "phase": "prepare", "status": "running", "message": "loading creds",
    }]


@pytest.mark.asyncio
async def test_write_async_optional_message_defaults_none():
    repo = StubRepo()
    w = ProgressWriter(repo, pool_id=uuid4(), node_id=None)
    await w.write_async("prepare", "succeeded")
    assert repo.calls[0]["message"] is None


@pytest.mark.asyncio
async def test_sync_write_from_other_thread_dispatches_to_loop():
    """sync write must enqueue the coroutine onto the captured loop,
    survive being called from a non-event-loop thread, and not block
    the caller forever."""
    import threading
    repo = StubRepo()
    pool = uuid4()
    loop = asyncio.get_running_loop()
    w = ProgressWriter(repo, pool_id=pool, node_id=None, loop=loop)
    done = threading.Event()

    def run_in_thread():
        w.write("pulumi_up", "log", "creating ec2")
        done.set()

    t = threading.Thread(target=run_in_thread)
    t.start()
    # Yield control to the event loop so the scheduled coroutine runs.
    for _ in range(10):
        if repo.calls:
            break
        await asyncio.sleep(0.01)
    done.wait(timeout=1.0)
    t.join(timeout=1.0)
    assert repo.calls[0]["phase"] == "pulumi_up"
    assert repo.calls[0]["status"] == "log"
    assert repo.calls[0]["message"] == "creating ec2"


@pytest.mark.asyncio
async def test_sync_write_swallows_repo_errors():
    """If append_event raises, the write must not propagate to the
    Pulumi thread (would break the up() call)."""
    class BoomRepo:
        async def append_event(self, **kw):
            raise RuntimeError("db gone")
    w = ProgressWriter(BoomRepo(), pool_id=uuid4(), node_id=None,
                       loop=asyncio.get_running_loop())
    import threading
    t = threading.Thread(target=lambda: w.write("pulumi_up", "log", "x"))
    t.start()
    t.join(timeout=1.0)
    await asyncio.sleep(0.05)  # let the scheduled coro hit its except


@pytest.mark.asyncio
async def test_message_truncated_to_1kib():
    repo = StubRepo()
    w = ProgressWriter(repo, pool_id=uuid4(), node_id=None)
    big = "x" * 5000
    await w.write_async("pulumi_up", "log", big)
    assert len(repo.calls[0]["message"]) == 1024


@pytest.mark.asyncio
async def test_message_none_passes_through():
    repo = StubRepo()
    w = ProgressWriter(repo, pool_id=uuid4(), node_id=None)
    await w.write_async("ready", "succeeded", None)
    assert repo.calls[0]["message"] is None
```

- [ ] **Step 2: Run, confirm failure**

```bash
PYTHONPATH=package/src python -m pytest \
  package/src/inferia/services/orchestration/services/adapter_engine/adapters/pulumi/tests/test_progress_writer.py -v
```

Expected: ImportError.

- [ ] **Step 3: Implement `ProgressWriter`**

Create `progress_writer.py`:

```python
"""Thread-safe bridge between Pulumi's synchronous on_event callback
and the asyncio repo. Captures the running loop at construction; the
sync `write()` method schedules a coroutine via
`asyncio.run_coroutine_threadsafe`, the async `write_async()` method
awaits inline.

Errors from the underlying repo are swallowed in the sync path so a
write failure cannot crash the Pulumi up() thread.
"""
from __future__ import annotations
import asyncio
import logging
from typing import Optional
from uuid import UUID

logger = logging.getLogger(__name__)

_MAX_MESSAGE_BYTES = 1024


def _truncate(msg: Optional[str]) -> Optional[str]:
    if msg is None:
        return None
    if len(msg) <= _MAX_MESSAGE_BYTES:
        return msg
    return msg[:_MAX_MESSAGE_BYTES]


class ProgressWriter:
    def __init__(
        self,
        repo,
        *,
        pool_id: UUID,
        node_id: Optional[UUID],
        loop: Optional[asyncio.AbstractEventLoop] = None,
    ):
        self._repo = repo
        self._pool_id = pool_id
        self._node_id = node_id
        try:
            self._loop = loop or asyncio.get_running_loop()
        except RuntimeError:
            self._loop = None

    async def write_async(
        self, phase: str, status: str, message: Optional[str] = None,
    ) -> None:
        await self._repo.append_event(
            pool_id=self._pool_id,
            node_id=self._node_id,
            phase=phase,
            status=status,
            message=_truncate(message),
        )

    def write(self, phase: str, status: str, message: Optional[str] = None) -> None:
        """Synchronous write — safe to call from the Pulumi thread."""
        if self._loop is None:
            logger.warning("progress writer has no loop; dropping event %s/%s",
                           phase, status)
            return
        async def _run():
            try:
                await self.write_async(phase, status, message)
            except Exception as e:
                logger.warning("progress event write failed: %s", e)
        try:
            asyncio.run_coroutine_threadsafe(_run(), self._loop)
        except Exception as e:
            logger.warning("could not schedule progress event: %s", e)
```

- [ ] **Step 4: Run tests, confirm pass**

```bash
PYTHONPATH=package/src python -m pytest \
  package/src/inferia/services/orchestration/services/adapter_engine/adapters/pulumi/tests/test_progress_writer.py -v
```

Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
cd /storage/intern/hooman/work/InferiaLLM
git add package/src/inferia/services/orchestration/services/adapter_engine/adapters/pulumi/progress_writer.py \
        package/src/inferia/services/orchestration/services/adapter_engine/adapters/pulumi/tests/test_progress_writer.py
git -c user.signingkey=/home/ankit/.ssh/id_ed25519_gh commit -S \
  -m "orchestration/pulumi: add thread-safe ProgressWriter"
```

---

## Task 4: Adapter — wire `progress_writer` through `provision_node` and `_provision_async`

**Files:**
- Modify: `package/src/inferia/services/orchestration/services/adapter_engine/adapters/pulumi/pulumi_aws_adapter.py`
- Create: `package/src/inferia/services/orchestration/services/adapter_engine/adapters/pulumi/tests/test_pulumi_aws_adapter_progress.py`

- [ ] **Step 1: Write failing tests**

Create `test_pulumi_aws_adapter_progress.py`:

```python
"""Verify PulumiAWSAdapter emits the 8-phase progress event sequence.

Mocks `pulumi.automation.create_or_select_stack` and `stack.up` so the
test never touches AWS or the Pulumi runtime. The adapter calls
progress_writer.write_async at phase boundaries and forwards an
on_event callback into stack.up that we can verify is wired.
"""
from __future__ import annotations
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from inferia.services.orchestration.services.adapter_engine.adapters.pulumi.pulumi_aws_adapter import (
    PulumiAWSAdapter,
    ProvisionError,
)


class RecordingWriter:
    def __init__(self):
        self.calls = []  # list of (phase, status, message)
    async def write_async(self, phase, status, message=None):
        self.calls.append((phase, status, message))
    def write(self, phase, status, message=None):
        self.calls.append((phase, status, message))


def _ok_outputs():
    # The adapter's _extract_output expects values with a .value attr
    # OR raw values. Mix both shapes for coverage.
    return {
        "instance_id": SimpleNamespace(value="i-abc123"),
        "public_dns":  SimpleNamespace(value="ec2-1.amazonaws.com"),
        "private_ip":  SimpleNamespace(value="10.0.0.5"),
    }


def _fake_providers_config():
    from inferia.services.api_gateway.config import ProvidersConfig, CloudConfig, AWSConfig
    return ProvidersConfig(cloud=CloudConfig(aws=AWSConfig(
        access_key_id="AKIA_TEST",
        secret_access_key="secret_test",
        region="us-east-1",
        ami_id="ami-stub",  # skip ami lookup
    )))


@pytest.mark.asyncio
async def test_provision_node_emits_eight_phase_sequence_on_success():
    adapter = PulumiAWSAdapter(state_dir="/tmp/pulumi-test")
    writer = RecordingWriter()
    pool = str(uuid4())
    org = str(uuid4())

    fake_stack = MagicMock()
    up_result = MagicMock()
    up_result.outputs = _ok_outputs()
    fake_stack.up.return_value = up_result

    db = AsyncMock()
    db.execute = AsyncMock()
    adapter._db = db

    with patch.object(adapter, "ensure_state_dir"), \
         patch("inferia.services.orchestration.services.adapter_engine.adapters.pulumi.pulumi_aws_adapter.load_providers_config",
               new=AsyncMock(return_value=_fake_providers_config())), \
         patch("inferia.services.orchestration.services.adapter_engine.adapters.pulumi.pulumi_aws_adapter.mint_bootstrap_token",
               new=AsyncMock(return_value=("tok", uuid4()))), \
         patch("inferia.services.orchestration.services.adapter_engine.adapters.pulumi.pulumi_aws_adapter.pulumi.automation.create_or_select_stack",
               return_value=fake_stack):
        result = await adapter.provision_node(
            provider_resource_id="t3.micro",
            pool_id=pool,
            org_id=org,
            progress_writer=writer,
        )

    assert result["lifecycle_state"] == "provisioning"
    # The background task needs the loop to drain
    for _ in range(50):
        await asyncio.sleep(0.02)
        if any(c[0] == "ready" for c in writer.calls):
            break
    phases = [c[0] for c in writer.calls]
    assert phases[:6] == ["prepare", "prepare",
                          "pulumi_init", "pulumi_init",
                          "pulumi_up", "pulumi_up"]
    # ami_lookup is skipped because ami_id is pinned in providers config
    statuses_for_each = {p: [s for ph, s, _ in writer.calls if ph == p]
                         for p in set(phases)}
    assert statuses_for_each["prepare"] == ["running", "succeeded"]
    assert statuses_for_each["pulumi_init"] == ["running", "succeeded"]
    # ami_lookup never emits when AMI is pinned
    assert "ami_lookup" not in statuses_for_each


@pytest.mark.asyncio
async def test_provision_node_writes_ami_lookup_phase_when_ami_not_pinned():
    adapter = PulumiAWSAdapter(state_dir="/tmp/pulumi-test")
    writer = RecordingWriter()
    fake_stack = MagicMock()
    up_result = MagicMock(); up_result.outputs = _ok_outputs()
    fake_stack.up.return_value = up_result
    cfg = _fake_providers_config()
    cfg.cloud.aws.ami_id = None  # force lookup

    with patch.object(adapter, "ensure_state_dir"), \
         patch("inferia.services.orchestration.services.adapter_engine.adapters.pulumi.pulumi_aws_adapter.load_providers_config",
               new=AsyncMock(return_value=cfg)), \
         patch("inferia.services.orchestration.services.adapter_engine.adapters.pulumi.pulumi_aws_adapter.mint_bootstrap_token",
               new=AsyncMock(return_value=("tok", uuid4()))), \
         patch("inferia.services.orchestration.services.adapter_engine.adapters.pulumi.pulumi_aws_adapter.latest_dlami_ami",
               return_value="ami-found"), \
         patch("inferia.services.orchestration.services.adapter_engine.adapters.pulumi.pulumi_aws_adapter.pulumi.automation.create_or_select_stack",
               return_value=fake_stack):
        await adapter.provision_node(
            provider_resource_id="t3.micro",
            pool_id=str(uuid4()), org_id=str(uuid4()),
            progress_writer=writer,
        )
    phase_statuses = [(p, s) for p, s, _ in writer.calls]
    assert ("ami_lookup", "running") in phase_statuses
    assert ("ami_lookup", "succeeded") in phase_statuses


@pytest.mark.asyncio
async def test_pulumi_up_failure_emits_failed_status():
    adapter = PulumiAWSAdapter(state_dir="/tmp/pulumi-test")
    writer = RecordingWriter()
    fake_stack = MagicMock()
    fake_stack.up.side_effect = RuntimeError("aws: insufficient capacity")

    db = AsyncMock(); db.execute = AsyncMock(); adapter._db = db

    with patch.object(adapter, "ensure_state_dir"), \
         patch("inferia.services.orchestration.services.adapter_engine.adapters.pulumi.pulumi_aws_adapter.load_providers_config",
               new=AsyncMock(return_value=_fake_providers_config())), \
         patch("inferia.services.orchestration.services.adapter_engine.adapters.pulumi.pulumi_aws_adapter.mint_bootstrap_token",
               new=AsyncMock(return_value=("tok", uuid4()))), \
         patch("inferia.services.orchestration.services.adapter_engine.adapters.pulumi.pulumi_aws_adapter.pulumi.automation.create_or_select_stack",
               return_value=fake_stack):
        await adapter.provision_node(
            provider_resource_id="t3.micro",
            pool_id=str(uuid4()), org_id=str(uuid4()),
            progress_writer=writer,
        )
    for _ in range(50):
        await asyncio.sleep(0.02)
        if any(s == "failed" for _, s, _ in writer.calls):
            break
    fails = [c for c in writer.calls if c[1] == "failed"]
    assert len(fails) == 1
    assert fails[0][0] == "pulumi_up"
    assert "insufficient capacity" in (fails[0][2] or "")


@pytest.mark.asyncio
async def test_no_writer_provided_falls_back_to_noop():
    """The lazy-deploy path (no progress_writer) must still work."""
    adapter = PulumiAWSAdapter(state_dir="/tmp/pulumi-test")
    fake_stack = MagicMock()
    fake_stack.up.return_value = MagicMock(outputs=_ok_outputs())
    with patch.object(adapter, "ensure_state_dir"), \
         patch("inferia.services.orchestration.services.adapter_engine.adapters.pulumi.pulumi_aws_adapter.load_providers_config",
               new=AsyncMock(return_value=_fake_providers_config())), \
         patch("inferia.services.orchestration.services.adapter_engine.adapters.pulumi.pulumi_aws_adapter.mint_bootstrap_token",
               new=AsyncMock(return_value=("tok", uuid4()))), \
         patch("inferia.services.orchestration.services.adapter_engine.adapters.pulumi.pulumi_aws_adapter.pulumi.automation.create_or_select_stack",
               return_value=fake_stack):
        result = await adapter.provision_node(
            provider_resource_id="t3.micro",
            pool_id=str(uuid4()), org_id=str(uuid4()),
        )
    assert result["lifecycle_state"] == "provisioning"


@pytest.mark.asyncio
async def test_pulumi_up_called_with_on_event_callback():
    adapter = PulumiAWSAdapter(state_dir="/tmp/pulumi-test")
    writer = RecordingWriter()
    fake_stack = MagicMock()
    fake_stack.up.return_value = MagicMock(outputs=_ok_outputs())
    with patch.object(adapter, "ensure_state_dir"), \
         patch("inferia.services.orchestration.services.adapter_engine.adapters.pulumi.pulumi_aws_adapter.load_providers_config",
               new=AsyncMock(return_value=_fake_providers_config())), \
         patch("inferia.services.orchestration.services.adapter_engine.adapters.pulumi.pulumi_aws_adapter.mint_bootstrap_token",
               new=AsyncMock(return_value=("tok", uuid4()))), \
         patch("inferia.services.orchestration.services.adapter_engine.adapters.pulumi.pulumi_aws_adapter.pulumi.automation.create_or_select_stack",
               return_value=fake_stack):
        await adapter.provision_node(
            provider_resource_id="t3.micro",
            pool_id=str(uuid4()), org_id=str(uuid4()),
            progress_writer=writer,
        )
        for _ in range(20):
            await asyncio.sleep(0.02)
            if fake_stack.up.called:
                break
    args, kwargs = fake_stack.up.call_args
    assert "on_event" in kwargs
    assert callable(kwargs["on_event"])
```

- [ ] **Step 2: Run, confirm failure**

```bash
PYTHONPATH=package/src python -m pytest \
  package/src/inferia/services/orchestration/services/adapter_engine/adapters/pulumi/tests/test_pulumi_aws_adapter_progress.py -v
```

Expected: failures saying `provision_node()` got an unexpected keyword argument `progress_writer`.

- [ ] **Step 3: Modify the adapter to accept `progress_writer`**

Edit `pulumi_aws_adapter.py`. At the top of the file, add:

```python
from inferia.services.orchestration.services.adapter_engine.adapters.pulumi.progress_writer import (
    ProgressWriter,
)
```

Add a `_NoopWriter` class right above `class PulumiAWSAdapter`:

```python
class _NoopWriter:
    async def write_async(self, *a, **kw): pass
    def write(self, *a, **kw): pass
```

Update `provision_node` signature (line 98) to add `progress_writer`:

```python
    async def provision_node(
        self,
        *,
        provider_resource_id: str,
        pool_id: str,
        org_id: str,
        region: Optional[str] = None,
        use_spot: bool = False,
        metadata: Optional[Dict[str, Any]] = None,
        provider_credential_name: Optional[str] = None,
        progress_writer: Any = None,
    ) -> Dict[str, Any]:
        writer = progress_writer or _NoopWriter()
```

Right after the writer assignment, wrap each phase boundary:

```python
        await writer.write_async("prepare", "running")
        cfg = await load_providers_config()
        env_vars = resolve_aws_env(cfg)  # raises MissingCredentialsError

        pool_meta = dict(metadata or {})
        if pool_meta:
            try:
                AWSPoolMetadata(**pool_meta)
            except Exception as e:
                await writer.write_async("prepare", "failed", str(e))
                raise ProvisionError(f"invalid AWS metadata: {e}") from e

        account = cfg.cloud.aws
        region = region or account.region or "us-east-1"
        subnet_id = pool_meta.get("subnet_id") or account.subnet_id
        sg_ids = pool_meta.get("security_group_ids") or account.security_group_ids
        ami_id = pool_meta.get("ami_id") or account.ami_id
        iam_arn = pool_meta.get("iam_instance_profile") or account.iam_instance_profile
        root_gb = pool_meta.get("root_volume_gb") or account.root_volume_gb or 100
        image_tag = (
            pool_meta.get("worker_image_tag")
            or account.worker_image_tag
            or settings.worker_image_tag
        )
        await writer.write_async("prepare", "succeeded")

        if not ami_id:
            await writer.write_async("ami_lookup", "running")
            from inferia.services.orchestration.services.adapter_engine.adapters.pulumi.ami import (
                PLAIN_UBUNTU_PARAMETER,
            )
            is_gpu_family = provider_resource_id.split(".")[0].lower() in {
                "g5", "g5g", "g6", "g6e", "g6f", "g4dn", "g4ad", "p4d", "p4de",
                "p5", "p5e", "p5en", "p3", "p3dn", "p2", "dl1", "dl2q", "trn1",
                "trn1n", "trn2",
            }
            param = None if is_gpu_family else PLAIN_UBUNTU_PARAMETER
            try:
                ami_id = latest_dlami_ami(
                    region,
                    aws_access_key_id=env_vars["AWS_ACCESS_KEY_ID"],
                    aws_secret_access_key=env_vars["AWS_SECRET_ACCESS_KEY"],
                    parameter_name=param,
                )
                await writer.write_async("ami_lookup", "succeeded", ami_id)
            except AMILookupError as e:
                await writer.write_async("ami_lookup", "failed", str(e))
                raise ProvisionError(f"AMI lookup failed: {e}") from e

        self.ensure_state_dir()

        # ... (keep existing bootstrap-token + user-data + program build) ...

        await writer.write_async("pulumi_init", "running")
        # ... existing create_or_select_stack block ...
        stack.set_config("aws:region", pulumi.automation.ConfigValue(region))
        await writer.write_async("pulumi_init", "succeeded")

        await writer.write_async("pulumi_up", "running")
        asyncio.create_task(self._provision_async(stack, pool_id, str(bootstrap_id), writer))
```

Update `_provision_async` (line 272):

```python
    async def _provision_async(
        self,
        stack: Any,
        pool_id: str,
        bootstrap_id: str,
        writer: Any = None,
    ) -> None:
        writer = writer or _NoopWriter()
        def _on_event(ev):
            try:
                kind = next(
                    (k for k in ("resource_pre_event", "res_outputs_event",
                                 "diagnostic_event", "summary_event")
                     if hasattr(ev, k) and getattr(ev, k) is not None),
                    "engine_event",
                )
                payload = str(getattr(ev, kind, ev))
                writer.write("pulumi_up", "log", f"{kind}: {payload}")
            except Exception:
                pass
        try:
            result = await asyncio.to_thread(stack.up, on_event=_on_event)
            outputs = result.outputs or {}
            instance_id = self._extract_output(outputs, "instance_id")
            public_dns  = self._extract_output(outputs, "public_dns")
            private_ip  = self._extract_output(outputs, "private_ip")
            meta_update = {
                "instance_id": instance_id,
                "public_dns":  public_dns,
                "private_ip":  private_ip,
            }
            await writer.write_async("pulumi_up", "succeeded", instance_id)
            await writer.write_async("ec2_running", "succeeded", public_dns)
            if self._db is not None:
                await self._db.execute(
                    "UPDATE compute_pools "
                    "SET metadata = COALESCE(metadata, '{}'::jsonb) || $1::jsonb "
                    "WHERE id = $2",
                    json.dumps(meta_update),
                    UUID(pool_id),
                )
                # Promote the placeholder inventory row to point at the
                # real EC2 instance. After this UPDATE the worker's later
                # register_worker call upserts on (provider, provider_instance_id)
                # and finds the same row, flipping state -> ready.
                if instance_id:
                    await self._db.execute(
                        "UPDATE compute_inventory "
                        "SET provider_instance_id = $1, hostname = $2, "
                        "    updated_at = now() "
                        "WHERE pool_id = $3 AND provider_instance_id LIKE 'placeholder:%'",
                        instance_id,
                        public_dns or "",
                        UUID(pool_id),
                    )
            await writer.write_async("worker_bootstrap", "running")
            logger.info("Pulumi up succeeded for pool %s: instance %s",
                        pool_id, instance_id)
        except Exception as e:
            err = str(e)
            await writer.write_async("pulumi_up", "failed", err)
            logger.error("Pulumi up failed for pool %s: %s", pool_id, err)
            if self._db is not None:
                await self._db.execute(
                    "UPDATE compute_pools "
                    "SET lifecycle_state = 'failed', "
                    "    metadata = COALESCE(metadata, '{}'::jsonb) || $1::jsonb "
                    "WHERE id = $2",
                    json.dumps({"error": err}),
                    UUID(pool_id),
                )
                await self._db.execute(
                    "UPDATE compute_inventory "
                    "SET state = 'terminated', updated_at = now(), "
                    "    metadata = COALESCE(metadata, '{}'::jsonb) || $1::jsonb "
                    "WHERE pool_id = $2 AND provider_instance_id LIKE 'placeholder:%'",
                    json.dumps({"failure_reason": err}),
                    UUID(pool_id),
                )
            try:
                await asyncio.to_thread(stack.destroy)
            except Exception as de:
                logger.warning("destroy failed after up failure: %s", de)
```

- [ ] **Step 4: Run tests, confirm pass**

```bash
PYTHONPATH=package/src python -m pytest \
  package/src/inferia/services/orchestration/services/adapter_engine/adapters/pulumi/tests/test_pulumi_aws_adapter_progress.py -v
```

Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add package/src/inferia/services/orchestration/services/adapter_engine/adapters/pulumi/pulumi_aws_adapter.py \
        package/src/inferia/services/orchestration/services/adapter_engine/adapters/pulumi/tests/test_pulumi_aws_adapter_progress.py
git -c user.signingkey=/home/ankit/.ssh/id_ed25519_gh commit -S \
  -m "orchestration/pulumi-aws: emit phase progress events"
```

---

## Task 5: `/createpool` AWS eager-provision path

**Files:**
- Modify: `package/src/inferia/services/orchestration/services/model_deployment/deployment_server.py:1086-1132`
- Create: `package/src/inferia/services/orchestration/services/model_deployment/tests/test_createpool_aws_eager.py`

- [ ] **Step 1: Write failing tests**

Create `test_createpool_aws_eager.py`:

```python
"""POST /createpool with provider=aws must:
1. Insert a placeholder inventory row with state='provisioning' and gpu_total=0.
2. Call PulumiAWSAdapter.provision_node with a progress_writer.
3. Leave existing nosana/akash placeholder path with state='ready' unchanged.
"""
from __future__ import annotations
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from fastapi.testclient import TestClient

# Test exercises the route handler directly with mocks.

class FakeConn:
    def __init__(self):
        self.executes = []
    async def execute(self, q, *a):
        self.executes.append((q, a))
    async def close(self): pass


@pytest.mark.asyncio
async def test_createpool_aws_inserts_provisioning_placeholder(monkeypatch):
    from inferia.services.orchestration.services.model_deployment import deployment_server
    fake_conn = FakeConn()
    monkeypatch.setattr(
        "inferia.services.orchestration.services.model_deployment.deployment_server.asyncpg",
        MagicMock(connect=AsyncMock(return_value=fake_conn)),
        raising=False,
    )
    mock_adapter = MagicMock()
    mock_adapter.get_capabilities.return_value = MagicMock()
    mock_adapter.provision_node = AsyncMock(return_value={"lifecycle_state": "provisioning"})
    monkeypatch.setattr(deployment_server, "get_adapter", lambda p: mock_adapter)

    fake_channel = MagicMock()
    fake_stub = MagicMock()
    fake_stub.RegisterPool = AsyncMock(return_value=MagicMock(pool_id="pool-1"))
    monkeypatch.setattr(deployment_server, "_auth_channel",
                        MagicMock(return_value=MagicMock(
                            __aenter__=AsyncMock(return_value=fake_channel),
                            __aexit__=AsyncMock(return_value=None),
                        )))
    monkeypatch.setattr(
        deployment_server.compute_pool_pb2_grpc,
        "ComputePoolManagerStub",
        lambda channel: fake_stub,
    )
    monkeypatch.setattr(deployment_server, "log_audit_event", AsyncMock())

    req_body = {
        "pool_name": "aws-test",
        "owner_type": "user",
        "owner_id": "00000000-0000-0000-0000-000000000001",
        "provider": "aws",
        "allowed_gpu_types": ["t3.micro"],
        "gpu_count": 1,
    }
    request = MagicMock()
    request.headers = {"x-organization-id": None}

    resp = await deployment_server.create_pool(
        deployment_server.CreatePoolRequest(**req_body), request
    )
    assert resp == {"pool_id": "pool-1", "status": "CREATED"}
    inserts = [q for q, _ in fake_conn.executes if "INSERT INTO compute_inventory" in q]
    assert any("'provisioning'" in q for q in inserts), \
        f"expected placeholder INSERT with state='provisioning', got: {inserts}"
    mock_adapter.provision_node.assert_awaited_once()
    call = mock_adapter.provision_node.await_args
    assert call.kwargs.get("progress_writer") is not None


@pytest.mark.asyncio
async def test_createpool_nosana_keeps_ready_placeholder(monkeypatch):
    """Regression: non-AWS providers keep the old behaviour."""
    from inferia.services.orchestration.services.model_deployment import deployment_server
    fake_conn = FakeConn()
    monkeypatch.setattr(
        "inferia.services.orchestration.services.model_deployment.deployment_server.asyncpg",
        MagicMock(connect=AsyncMock(return_value=fake_conn)),
        raising=False,
    )
    mock_adapter = MagicMock()
    mock_adapter.get_capabilities.return_value = MagicMock()
    monkeypatch.setattr(deployment_server, "get_adapter", lambda p: mock_adapter)

    fake_stub = MagicMock()
    fake_stub.RegisterPool = AsyncMock(return_value=MagicMock(pool_id="pool-2"))
    monkeypatch.setattr(deployment_server, "_auth_channel",
                        MagicMock(return_value=MagicMock(
                            __aenter__=AsyncMock(return_value=MagicMock()),
                            __aexit__=AsyncMock(return_value=None),
                        )))
    monkeypatch.setattr(
        deployment_server.compute_pool_pb2_grpc,
        "ComputePoolManagerStub",
        lambda channel: fake_stub,
    )
    monkeypatch.setattr(deployment_server, "log_audit_event", AsyncMock())

    req_body = {
        "pool_name": "nosana-test",
        "owner_type": "user",
        "owner_id": "00000000-0000-0000-0000-000000000001",
        "provider": "nosana",
        "allowed_gpu_types": ["a100"],
        "gpu_count": 1,
    }
    request = MagicMock(); request.headers = {"x-organization-id": None}
    await deployment_server.create_pool(
        deployment_server.CreatePoolRequest(**req_body), request
    )
    inserts = [q for q, _ in fake_conn.executes if "INSERT INTO compute_inventory" in q]
    assert any("'ready'" in q for q in inserts)
    mock_adapter.provision_node.assert_not_called()
```

- [ ] **Step 2: Run, confirm failure**

```bash
PYTHONPATH=package/src python -m pytest \
  package/src/inferia/services/orchestration/services/model_deployment/tests/test_createpool_aws_eager.py -v
```

Expected: AssertionError — placeholder still inserts `state='ready'` for AWS and `provision_node` not called.

- [ ] **Step 3: Modify `/createpool`**

Edit `package/src/inferia/services/orchestration/services/model_deployment/deployment_server.py` lines 1086-1132. Replace the existing `if req.provider in (...)` block with:

```python
    # Provider placeholder + (for AWS) eager provisioning kickoff.
    try:
        from uuid import UUID as _UUID
        import asyncpg as _asyncpg
        import os as _os
        dsn = (
            _os.getenv("POSTGRES_DSN")
            or (_os.getenv("DATABASE_URL", "").replace("postgresql+asyncpg://", "postgresql://", 1))
            or "postgresql://inferia:inferia@postgres:5432/inferia"
        )
        if req.provider == "aws":
            # Eager EC2 provisioning. Insert provisioning placeholder, then
            # call PulumiAWSAdapter.provision_node which returns immediately
            # and schedules the background pulumi up task.
            conn = await _asyncpg.connect(dsn=dsn, timeout=5)
            try:
                gpu_type = (req.allowed_gpu_types[0] if req.allowed_gpu_types else "any")
                await conn.execute(
                    """
                    INSERT INTO compute_inventory (
                        pool_id, provider, provider_instance_id, hostname,
                        gpu_total, vcpu_total, ram_gb_total, state,
                        node_class, metadata, labels
                    )
                    VALUES (
                        $1::uuid, $2::provider_type, $3, $4,
                        0, 0, 0, 'provisioning',
                        'on_demand', $5::jsonb, '{}'::jsonb
                    )
                    """,
                    resp.pool_id,
                    req.provider,
                    f"placeholder:{resp.pool_id}",
                    req.pool_name,
                    __import__("json").dumps({
                        "gpu_type": gpu_type,
                        "provider_pool_id": req.provider_pool_id,
                        "placeholder": True,
                        "requested_gpu_count": req.gpu_count or 1,
                    }),
                )
            finally:
                await conn.close()

            # Kick off Pulumi provisioning with a progress writer.
            from inferia.services.orchestration.repositories.node_provisioning_repo import (
                NodeProvisioningRepo,
            )
            from inferia.services.orchestration.services.adapter_engine.adapters.pulumi.progress_writer import (
                ProgressWriter,
            )
            from uuid import UUID as _UUID2
            pool_conn = await _asyncpg.connect(dsn=dsn, timeout=5)
            # Use a connection pool wrapper that supports .fetch/.fetchval/.execute
            class _SingleConnPool:
                def __init__(self, c): self._c = c
                async def fetchval(self, *a, **kw): return await self._c.fetchval(*a, **kw)
                async def fetch(self,    *a, **kw): return await self._c.fetch(*a, **kw)
                async def execute(self,  *a, **kw): return await self._c.execute(*a, **kw)
            prov_repo = NodeProvisioningRepo(_SingleConnPool(pool_conn))
            writer = ProgressWriter(
                prov_repo,
                pool_id=_UUID2(resp.pool_id),
                node_id=None,
            )
            adapter = get_adapter("aws")
            adapter._db = pool_conn  # adapter needs a connection for its UPDATE calls
            asyncio.create_task(_kick_aws_provision(
                adapter, req, resp.pool_id, writer, pool_conn,
            ))
        elif req.provider in ("nosana", "akash", "gcp", "azure", "lambda", "runpod", "k8s"):
            conn = await _asyncpg.connect(dsn=dsn, timeout=5)
            try:
                gpu_type = (req.allowed_gpu_types[0] if req.allowed_gpu_types else "any")
                await conn.execute(
                    """
                    INSERT INTO compute_inventory (
                        pool_id, provider, provider_instance_id, hostname,
                        gpu_total, vcpu_total, ram_gb_total, state,
                        node_class, metadata, labels
                    )
                    VALUES (
                        $1::uuid, $2::provider_type, $3, $4,
                        $5, 0, 0, 'ready',
                        'on_demand', $6::jsonb, '{}'::jsonb
                    )
                    """,
                    resp.pool_id,
                    req.provider,
                    f"placeholder:{resp.pool_id}",
                    req.pool_name,
                    req.gpu_count or 1,
                    __import__("json").dumps({
                        "gpu_type": gpu_type,
                        "provider_pool_id": req.provider_pool_id,
                        "placeholder": True,
                    }),
                )
            finally:
                await conn.close()
    except Exception as e:
        import logging as _logging
        _logging.getLogger("deployment-server").warning(
            "createpool: placeholder/provisioning kickoff failed: %s", e,
        )
```

Add the helper above `create_pool`:

```python
async def _kick_aws_provision(adapter, req, pool_id, writer, conn_to_close):
    try:
        await adapter.provision_node(
            provider_resource_id=(req.allowed_gpu_types[0] if req.allowed_gpu_types else "t3.micro"),
            pool_id=pool_id,
            org_id=req.owner_id,
            region=getattr(req, "region_constraint", None) or None,
            use_spot=bool(getattr(req, "use_spot", False)),
            progress_writer=writer,
        )
    except Exception as e:
        import logging as _logging
        _logging.getLogger("deployment-server").exception(
            "aws provisioning kickoff failed: %s", e,
        )
        try:
            await writer.write_async("prepare", "failed", str(e))
        except Exception:
            pass
```

Make sure `asyncio` is imported at the top of the file (it should already be).

- [ ] **Step 4: Run tests, confirm pass**

```bash
PYTHONPATH=package/src python -m pytest \
  package/src/inferia/services/orchestration/services/model_deployment/tests/test_createpool_aws_eager.py -v
```

Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add package/src/inferia/services/orchestration/services/model_deployment/deployment_server.py \
        package/src/inferia/services/orchestration/services/model_deployment/tests/test_createpool_aws_eager.py
git -c user.signingkey=/home/ankit/.ssh/id_ed25519_gh commit -S \
  -m "deployment/createpool: AWS path provisions EC2 eagerly with progress events"
```

---

## Task 6: REST endpoints — `/v1/nodes/{id}/provisioning`, `/provisioning-logs`, `/ec2-console`

**Files:**
- Modify: `package/src/inferia/services/orchestration/api/nodes.py`
- Modify: `package/src/inferia/services/orchestration/server.py:226-233`
- Create: `package/src/inferia/services/orchestration/api/tests/test_nodes_provisioning_endpoints.py`

- [ ] **Step 1: Write failing tests**

Create `test_nodes_provisioning_endpoints.py`:

```python
"""End-to-end tests for the three provisioning REST endpoints.

Uses FastAPI TestClient against an isolated app that wires nodes_api
with mock repos and a mock AWS adapter.
"""
from __future__ import annotations
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from inferia.services.orchestration.api import nodes as nodes_api


def _user_ctx_header():
    return {
        "authorization": "Bearer test",
        "x-organization-id": str(uuid4()),
    }


@pytest.fixture
def app_and_deps():
    inv = MagicMock()
    pool_id = uuid4()
    node_row = {
        "id": uuid4(), "pool_id": pool_id, "node_name": "n1",
        "agent_kind": None, "provider": "aws", "state": "provisioning",
        "labels": {}, "advertise_url": None, "expose_url": None,
        "gpu_total": 0, "gpu_allocated": 0, "vcpu_total": 0, "vcpu_allocated": 0,
        "ram_gb_total": 0, "ram_gb_allocated": 0, "last_heartbeat": None,
        "provider_instance_id": "placeholder:" + str(pool_id),
    }
    inv.get_node = AsyncMock(return_value=node_row)
    prov = MagicMock()
    prov.summarize_phases = AsyncMock(return_value=[
        {"phase": "prepare", "status": "succeeded",
         "started_at": datetime.now(timezone.utc),
         "ended_at":   datetime.now(timezone.utc),
         "last_message": None},
        {"phase": "pulumi_up", "status": "running",
         "started_at": datetime.now(timezone.utc),
         "ended_at": None,
         "last_message": "creating ec2"},
    ])
    prov.list_events_after = AsyncMock(return_value=[
        {"id": 1, "phase": "prepare", "status": "running",
         "message": None, "created_at": datetime.now(timezone.utc)},
        {"id": 2, "phase": "prepare", "status": "succeeded",
         "message": None, "created_at": datetime.now(timezone.utc)},
    ])
    prov.current_phase = AsyncMock(return_value="pulumi_up")
    aws_adapter = MagicMock()
    aws_adapter.get_logs = AsyncMock(return_value={
        "logs": ["[boot] cloud-init starting", "[user-data] docker pull..."],
    })
    app = FastAPI()
    nodes_api.configure(
        inventory_repo=inv, pool_repo=MagicMock(), worker_auth=MagicMock(),
        control_plane_external_url="", adapters={"aws": aws_adapter},
        require_permission=lambda _: (lambda: True),
        provisioning_repo=prov,
    )
    app.include_router(nodes_api.router, prefix="/v1/nodes")
    return app, inv, prov, aws_adapter, node_row


def test_get_provisioning_returns_phase_summary(app_and_deps):
    app, _, _, _, node_row = app_and_deps
    client = TestClient(app)
    r = client.get(f"/v1/nodes/{node_row['id']}/provisioning",
                   headers=_user_ctx_header())
    assert r.status_code == 200
    body = r.json()
    assert body["current_phase"] == "pulumi_up"
    assert body["terminal"] is False
    phases = {p["phase"]: p for p in body["phases"]}
    assert phases["prepare"]["status"] == "succeeded"
    assert phases["pulumi_up"]["status"] == "running"


def test_get_provisioning_logs_returns_events_after_cursor(app_and_deps):
    app, _, _, _, node_row = app_and_deps
    client = TestClient(app)
    r = client.get(f"/v1/nodes/{node_row['id']}/provisioning-logs?after=0",
                   headers=_user_ctx_header())
    assert r.status_code == 200
    body = r.json()
    assert [e["id"] for e in body["events"]] == [1, 2]
    assert body["next_after"] == 2


def test_get_provisioning_logs_empty_returns_null_cursor(app_and_deps):
    app, _, prov, _, node_row = app_and_deps
    prov.list_events_after = AsyncMock(return_value=[])
    client = TestClient(app)
    r = client.get(f"/v1/nodes/{node_row['id']}/provisioning-logs?after=99",
                   headers=_user_ctx_header())
    body = r.json()
    assert body["events"] == []
    assert body["next_after"] is None


def test_get_ec2_console_proxies_adapter(app_and_deps):
    app, _, _, aws_adapter, node_row = app_and_deps
    client = TestClient(app)
    r = client.get(f"/v1/nodes/{node_row['id']}/ec2-console",
                   headers=_user_ctx_header())
    assert r.status_code == 200
    body = r.json()
    assert body["logs"][0].startswith("[boot]")
    assert "fetched_at" in body
    aws_adapter.get_logs.assert_awaited_once()


def test_endpoints_404_when_node_missing(app_and_deps):
    app, inv, _, _, _ = app_and_deps
    inv.get_node = AsyncMock(return_value=None)
    client = TestClient(app)
    bogus = uuid4()
    for path in ("provisioning", "provisioning-logs", "ec2-console"):
        r = client.get(f"/v1/nodes/{bogus}/{path}", headers=_user_ctx_header())
        assert r.status_code == 404, path


def test_ec2_console_404_for_non_aws_node(app_and_deps):
    app, inv, _, _, node_row = app_and_deps
    node_row["provider"] = "worker"
    inv.get_node = AsyncMock(return_value=node_row)
    client = TestClient(app)
    r = client.get(f"/v1/nodes/{node_row['id']}/ec2-console",
                   headers=_user_ctx_header())
    assert r.status_code == 404
    assert "aws" in r.json()["detail"].lower()


def test_ec2_console_returns_empty_when_adapter_returns_no_logs(app_and_deps):
    app, _, _, aws_adapter, node_row = app_and_deps
    aws_adapter.get_logs = AsyncMock(return_value={"logs": []})
    client = TestClient(app)
    r = client.get(f"/v1/nodes/{node_row['id']}/ec2-console",
                   headers=_user_ctx_header())
    assert r.status_code == 200
    assert r.json()["logs"] == []


def test_provisioning_terminal_true_when_ready_succeeded(app_and_deps):
    app, _, prov, _, node_row = app_and_deps
    prov.summarize_phases = AsyncMock(return_value=[
        {"phase": "ready", "status": "succeeded",
         "started_at": datetime.now(timezone.utc),
         "ended_at": datetime.now(timezone.utc),
         "last_message": None},
    ])
    prov.current_phase = AsyncMock(return_value=None)
    client = TestClient(app)
    r = client.get(f"/v1/nodes/{node_row['id']}/provisioning",
                   headers=_user_ctx_header())
    assert r.json()["terminal"] is True
```

- [ ] **Step 2: Run, confirm failure**

```bash
PYTHONPATH=package/src python -m pytest \
  package/src/inferia/services/orchestration/api/tests/test_nodes_provisioning_endpoints.py -v
```

Expected: errors saying `configure()` got unexpected `provisioning_repo`, or 404 because routes don't exist.

- [ ] **Step 3: Extend `nodes_api.configure` and add the three routes**

In `package/src/inferia/services/orchestration/api/nodes.py`:

Find the module-level `_deps` declaration (search for `class _Deps` or wherever `inventory_repo` is stored). Add a `provisioning_repo` slot. Locate `def configure(...)` and add `provisioning_repo=None` parameter, store on `_deps`.

Add new response models near the existing ones:

```python
class ProvisioningPhase(BaseModel):
    phase: str
    status: str
    started_at: str | None = None
    ended_at: str | None = None
    last_message: str | None = None


class ProvisioningSummary(BaseModel):
    current_phase: str | None = None
    terminal: bool
    phases: list[ProvisioningPhase]


class ProvisioningEvent(BaseModel):
    id: int
    phase: str
    status: str
    message: str | None = None
    created_at: str


class ProvisioningLogsResponse(BaseModel):
    events: list[ProvisioningEvent]
    next_after: int | None = None


class EC2ConsoleResponse(BaseModel):
    logs: list[str]
    fetched_at: str
```

Add the routes at the bottom of the routes section:

```python
@router.get("/{node_id}/provisioning", response_model=ProvisioningSummary)
async def get_provisioning(
    node_id: str = Path(...),
    _granted: bool = Depends(_need_perm("deployment:list")),
):
    row = await _deps.inventory_repo.get_node(node_id=node_id)
    if not row:
        raise HTTPException(404, "node not found")
    pool_id = row.get("pool_id")
    if not pool_id or _deps.provisioning_repo is None:
        return ProvisioningSummary(current_phase=None, terminal=True, phases=[])
    summary = await _deps.provisioning_repo.summarize_phases(pool_id=pool_id)
    current = await _deps.provisioning_repo.current_phase(pool_id=pool_id)
    terminal = current is None
    return ProvisioningSummary(
        current_phase=current,
        terminal=terminal,
        phases=[ProvisioningPhase(
            phase=p["phase"], status=p["status"],
            started_at=p["started_at"].isoformat() if p["started_at"] else None,
            ended_at=p["ended_at"].isoformat() if p["ended_at"] else None,
            last_message=p["last_message"],
        ) for p in summary],
    )


@router.get("/{node_id}/provisioning-logs", response_model=ProvisioningLogsResponse)
async def get_provisioning_logs(
    node_id: str = Path(...),
    after: int = Query(default=0, ge=0),
    limit: int = Query(default=500, ge=1, le=2000),
    _granted: bool = Depends(_need_perm("deployment:list")),
):
    row = await _deps.inventory_repo.get_node(node_id=node_id)
    if not row:
        raise HTTPException(404, "node not found")
    if _deps.provisioning_repo is None:
        return ProvisioningLogsResponse(events=[], next_after=None)
    events = await _deps.provisioning_repo.list_events_after(
        pool_id=row["pool_id"], after_id=after, limit=limit,
    )
    next_after = events[-1]["id"] if events else None
    return ProvisioningLogsResponse(
        events=[ProvisioningEvent(
            id=e["id"], phase=e["phase"], status=e["status"],
            message=e["message"],
            created_at=e["created_at"].isoformat(),
        ) for e in events],
        next_after=next_after,
    )


@router.get("/{node_id}/ec2-console", response_model=EC2ConsoleResponse)
async def get_ec2_console(
    node_id: str = Path(...),
    _granted: bool = Depends(_need_perm("deployment:list")),
):
    from datetime import datetime, timezone
    row = await _deps.inventory_repo.get_node(node_id=node_id)
    if not row:
        raise HTTPException(404, "node not found")
    if row.get("provider") != "aws":
        raise HTTPException(404, "ec2 console only available for aws provider")
    adapter = _deps.adapters.get("aws") if _deps.adapters else None
    if adapter is None:
        raise HTTPException(503, "aws adapter not configured")
    instance_id = row.get("provider_instance_id") or ""
    if instance_id.startswith("placeholder:"):
        return EC2ConsoleResponse(
            logs=[], fetched_at=datetime.now(timezone.utc).isoformat(),
        )
    result = await adapter.get_logs(provider_instance_id=instance_id)
    return EC2ConsoleResponse(
        logs=result.get("logs", []),
        fetched_at=datetime.now(timezone.utc).isoformat(),
    )
```

In `server.py:226-233`, modify the `nodes_api.configure(...)` call:

```python
    from inferia.services.orchestration.repositories.node_provisioning_repo import (
        NodeProvisioningRepo,
    )
    provisioning_repo = NodeProvisioningRepo(db_pool)  # use existing pool
    # Add AWS adapter so /ec2-console can fetch console output
    aws_cls = ADAPTER_REGISTRY.get("aws")
    if aws_cls is not None:
        try:
            nodes_adapters["aws"] = aws_cls()
        except Exception as e:
            logger.warning("could not instantiate aws adapter for /v1/nodes: %s", e)
    nodes_api.configure(
        inventory_repo=inventory_repo,
        pool_repo=pool_repo,
        worker_auth=worker_auth,
        control_plane_external_url=os.getenv("CONTROL_PLANE_EXTERNAL_URL", ""),
        adapters=nodes_adapters,
        require_permission=_permit_all,
        provisioning_repo=provisioning_repo,
    )
```

Use whatever variable name the existing `inventory_repo` uses for the asyncpg pool (read the file above the call to confirm; pass the same pool into `NodeProvisioningRepo`).

- [ ] **Step 4: Run tests, confirm pass**

```bash
PYTHONPATH=package/src python -m pytest \
  package/src/inferia/services/orchestration/api/tests/test_nodes_provisioning_endpoints.py -v
```

Expected: 8 passed.

- [ ] **Step 5: Integration test — confirm routes registered**

Add to the existing wire-up integration test (per memory `wire-handlers-check`). Look in `package/src/inferia/services/orchestration/api/tests/test_nodes_integration.py` (or similar). If absent, add a one-liner check:

```python
def test_provisioning_routes_registered():
    from inferia.services.orchestration.api import nodes as nodes_api
    paths = [r.path for r in nodes_api.router.routes]
    assert any(p.endswith("/provisioning") for p in paths)
    assert any(p.endswith("/provisioning-logs") for p in paths)
    assert any(p.endswith("/ec2-console") for p in paths)
```

- [ ] **Step 6: Commit**

```bash
git add package/src/inferia/services/orchestration/api/nodes.py \
        package/src/inferia/services/orchestration/server.py \
        package/src/inferia/services/orchestration/api/tests/test_nodes_provisioning_endpoints.py
git -c user.signingkey=/home/ankit/.ssh/id_ed25519_gh commit -S \
  -m "api/nodes: add /provisioning, /provisioning-logs, /ec2-console endpoints"
```

---

## Task 7: Frontend service — `provisioningService.ts`

**Files:**
- Create: `apps/dashboard/src/services/provisioningService.ts`
- Create: `apps/dashboard/src/services/provisioningService.test.ts`

- [ ] **Step 1: Write failing tests**

Create `provisioningService.test.ts`:

```typescript
import { describe, it, expect, vi, beforeEach } from "vitest";
import {
  getProvisioning,
  getProvisioningLogs,
  getEC2Console,
  type ProvisioningSummary,
} from "./provisioningService";

const fetchMock = vi.fn();
vi.mock("./api", () => ({
  computeApi: {
    get: (path: string) => fetchMock(path).then((r: any) => ({ data: r })),
  },
}));

describe("provisioningService", () => {
  beforeEach(() => fetchMock.mockReset());

  it("getProvisioning returns the summary shape", async () => {
    const payload: ProvisioningSummary = {
      current_phase: "pulumi_up",
      terminal: false,
      phases: [{ phase: "prepare", status: "succeeded",
                 started_at: "2026-05-25T00:00:00Z",
                 ended_at: "2026-05-25T00:00:01Z",
                 last_message: null }],
    };
    fetchMock.mockResolvedValueOnce(payload);
    const s = await getProvisioning("node-1");
    expect(s).toEqual(payload);
    expect(fetchMock).toHaveBeenCalledWith("/nodes/node-1/provisioning");
  });

  it("getProvisioningLogs passes the cursor", async () => {
    fetchMock.mockResolvedValueOnce({ events: [], next_after: null });
    await getProvisioningLogs("node-1", 42);
    expect(fetchMock).toHaveBeenCalledWith(
      "/nodes/node-1/provisioning-logs?after=42",
    );
  });

  it("getProvisioningLogs defaults cursor to 0", async () => {
    fetchMock.mockResolvedValueOnce({ events: [], next_after: null });
    await getProvisioningLogs("node-1");
    expect(fetchMock).toHaveBeenCalledWith(
      "/nodes/node-1/provisioning-logs?after=0",
    );
  });

  it("getEC2Console returns lines + fetched_at", async () => {
    fetchMock.mockResolvedValueOnce({
      logs: ["[boot] line1"], fetched_at: "2026-05-25T00:00:00Z",
    });
    const c = await getEC2Console("node-1");
    expect(c.logs).toEqual(["[boot] line1"]);
  });
});
```

- [ ] **Step 2: Run, confirm failure**

```bash
cd /storage/intern/hooman/work/InferiaLLM/apps/dashboard
npx vitest run src/services/provisioningService.test.ts
```

Expected: module not found.

- [ ] **Step 3: Implement the service**

Create `provisioningService.ts`:

```typescript
import { computeApi } from "./api";

export type PhaseStatus = "pending" | "running" | "succeeded" | "failed";

export interface ProvisioningPhase {
  phase: string;
  status: PhaseStatus;
  started_at: string | null;
  ended_at: string | null;
  last_message: string | null;
}

export interface ProvisioningSummary {
  current_phase: string | null;
  terminal: boolean;
  phases: ProvisioningPhase[];
}

export interface ProvisioningEvent {
  id: number;
  phase: string;
  status: PhaseStatus | "log";
  message: string | null;
  created_at: string;
}

export interface ProvisioningLogsResponse {
  events: ProvisioningEvent[];
  next_after: number | null;
}

export interface EC2ConsoleResponse {
  logs: string[];
  fetched_at: string;
}

export async function getProvisioning(nodeId: string): Promise<ProvisioningSummary> {
  const r = await computeApi.get(`/nodes/${nodeId}/provisioning`);
  return r.data;
}

export async function getProvisioningLogs(
  nodeId: string, after: number = 0,
): Promise<ProvisioningLogsResponse> {
  const r = await computeApi.get(`/nodes/${nodeId}/provisioning-logs?after=${after}`);
  return r.data;
}

export async function getEC2Console(nodeId: string): Promise<EC2ConsoleResponse> {
  const r = await computeApi.get(`/nodes/${nodeId}/ec2-console`);
  return r.data;
}

export const ALL_PHASES = [
  "prepare", "ami_lookup", "pulumi_init", "pulumi_up",
  "ec2_running", "cloud_init", "worker_bootstrap", "ready",
] as const;
```

- [ ] **Step 4: Run tests, confirm pass**

```bash
cd /storage/intern/hooman/work/InferiaLLM/apps/dashboard
npx vitest run src/services/provisioningService.test.ts
```

Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add apps/dashboard/src/services/provisioningService.ts \
        apps/dashboard/src/services/provisioningService.test.ts
git -c user.signingkey=/home/ankit/.ssh/id_ed25519_gh commit -S \
  -m "dashboard: add provisioningService"
```

---

## Task 8: Frontend — `ProvisioningStatus` component

**Files:**
- Create: `apps/dashboard/src/components/nodes/ProvisioningStatus.tsx`
- Create: `apps/dashboard/src/components/nodes/ProvisioningStatus.test.tsx`

- [ ] **Step 1: Write failing tests**

Create `ProvisioningStatus.test.tsx`:

```typescript
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import ProvisioningStatus from "./ProvisioningStatus";
import { ALL_PHASES, type ProvisioningSummary } from "@/services/provisioningService";

const baseSummary: ProvisioningSummary = {
  current_phase: "pulumi_up",
  terminal: false,
  phases: [
    { phase: "prepare", status: "succeeded",
      started_at: "2026-05-25T00:00:00Z", ended_at: "2026-05-25T00:00:02Z",
      last_message: null },
    { phase: "pulumi_up", status: "running",
      started_at: "2026-05-25T00:00:03Z", ended_at: null,
      last_message: "creating ec2" },
  ],
};

describe("ProvisioningStatus", () => {
  it("renders all 8 phases in order", () => {
    render(<ProvisioningStatus summary={baseSummary} />);
    const phases = screen.getAllByTestId(/^phase-row-/);
    expect(phases).toHaveLength(ALL_PHASES.length);
    ALL_PHASES.forEach((p, i) => {
      expect(phases[i]).toHaveAttribute("data-testid", `phase-row-${p}`);
    });
  });

  it("running phase shows spinner icon", () => {
    render(<ProvisioningStatus summary={baseSummary} />);
    const row = screen.getByTestId("phase-row-pulumi_up");
    expect(row.querySelector('[data-icon="spinner"]')).not.toBeNull();
  });

  it("succeeded phase shows check icon", () => {
    render(<ProvisioningStatus summary={baseSummary} />);
    const row = screen.getByTestId("phase-row-prepare");
    expect(row.querySelector('[data-icon="check"]')).not.toBeNull();
  });

  it("pending phase shows dim circle icon", () => {
    render(<ProvisioningStatus summary={baseSummary} />);
    const row = screen.getByTestId("phase-row-ready");
    expect(row.querySelector('[data-icon="pending"]')).not.toBeNull();
  });

  it("failed phase shows error icon and red banner", () => {
    const failed: ProvisioningSummary = {
      current_phase: null, terminal: true,
      phases: [
        { phase: "pulumi_up", status: "failed",
          started_at: "2026-05-25T00:00:00Z", ended_at: "2026-05-25T00:00:10Z",
          last_message: "insufficient capacity" },
      ],
    };
    render(<ProvisioningStatus summary={failed} />);
    const row = screen.getByTestId("phase-row-pulumi_up");
    expect(row.querySelector('[data-icon="error"]')).not.toBeNull();
    expect(screen.getByText(/insufficient capacity/)).toBeInTheDocument();
  });

  it("displays the running phase's last_message", () => {
    render(<ProvisioningStatus summary={baseSummary} />);
    expect(screen.getByText("creating ec2")).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run, confirm failure**

```bash
cd /storage/intern/hooman/work/InferiaLLM/apps/dashboard
npx vitest run src/components/nodes/ProvisioningStatus.test.tsx
```

Expected: file not found.

- [ ] **Step 3: Implement the component**

Create `ProvisioningStatus.tsx`:

```typescript
import { CheckCircle2, Circle, Loader2, XCircle } from "lucide-react";
import { cn } from "@/lib/utils";
import { ALL_PHASES, type ProvisioningSummary, type ProvisioningPhase } from "@/services/provisioningService";

const PHASE_LABELS: Record<string, string> = {
  prepare: "Prepare credentials & user-data",
  ami_lookup: "Look up AMI",
  pulumi_init: "Initialize Pulumi stack",
  pulumi_up: "Provision EC2 instance",
  ec2_running: "EC2 instance running",
  cloud_init: "Boot & install worker",
  worker_bootstrap: "Worker bootstrap",
  ready: "Ready",
};

function PhaseIcon({ status }: { status: ProvisioningPhase["status"] | "pending" }) {
  if (status === "running") {
    return <Loader2 className="w-4 h-4 animate-spin text-ember-500" data-icon="spinner" />;
  }
  if (status === "succeeded") {
    return <CheckCircle2 className="w-4 h-4 text-emerald-500" data-icon="check" />;
  }
  if (status === "failed") {
    return <XCircle className="w-4 h-4 text-red-500" data-icon="error" />;
  }
  return <Circle className="w-4 h-4 text-muted-foreground/40" data-icon="pending" />;
}

export default function ProvisioningStatus({ summary }: { summary: ProvisioningSummary }) {
  const byPhase = new Map(summary.phases.map(p => [p.phase, p]));
  const failed = summary.phases.find(p => p.status === "failed");

  return (
    <div className="rounded-xl border bg-card text-card-foreground shadow-sm p-6">
      <h3 className="font-mono text-sm font-semibold mb-4">Provisioning Status</h3>
      {failed && (
        <div className="mb-4 rounded-md border border-red-500/30 bg-red-500/10 text-red-700 dark:text-red-300 px-3 py-2 text-sm">
          <div className="font-semibold">Provisioning failed at {PHASE_LABELS[failed.phase] || failed.phase}</div>
          {failed.last_message && (
            <div className="font-mono text-xs mt-1 break-all">{failed.last_message}</div>
          )}
        </div>
      )}
      <ol className="space-y-2">
        {ALL_PHASES.map((phase) => {
          const p = byPhase.get(phase);
          const status: ProvisioningPhase["status"] | "pending" = p?.status ?? "pending";
          return (
            <li
              key={phase}
              data-testid={`phase-row-${phase}`}
              className={cn(
                "flex items-start gap-3 text-sm",
                status === "pending" && "text-muted-foreground/60",
              )}
            >
              <div className="mt-0.5"><PhaseIcon status={status} /></div>
              <div className="flex-1">
                <div className="font-medium">{PHASE_LABELS[phase] || phase}</div>
                {status === "running" && p?.last_message && (
                  <div className="text-xs text-muted-foreground font-mono mt-0.5 break-all">
                    {p.last_message}
                  </div>
                )}
              </div>
            </li>
          );
        })}
      </ol>
    </div>
  );
}
```

- [ ] **Step 4: Run tests, confirm pass**

```bash
cd /storage/intern/hooman/work/InferiaLLM/apps/dashboard
npx vitest run src/components/nodes/ProvisioningStatus.test.tsx
```

Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add apps/dashboard/src/components/nodes/ProvisioningStatus.tsx \
        apps/dashboard/src/components/nodes/ProvisioningStatus.test.tsx
git -c user.signingkey=/home/ankit/.ssh/id_ed25519_gh commit -S \
  -m "dashboard: add ProvisioningStatus component"
```

---

## Task 9: Frontend — `NodeLogs` branching for AWS provisioning

**Files:**
- Modify: `apps/dashboard/src/components/nodes/NodeLogs.tsx`
- Create: `apps/dashboard/src/components/nodes/NodeLogs.test.tsx`

- [ ] **Step 1: Write failing tests**

Create `NodeLogs.test.tsx`:

```typescript
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, act } from "@testing-library/react";
import NodeLogs from "./NodeLogs";

vi.mock("@/services/provisioningService", () => ({
  getProvisioningLogs: vi.fn(),
  getEC2Console:       vi.fn(),
}));

const { getProvisioningLogs, getEC2Console } =
  await import("@/services/provisioningService");

describe("NodeLogs (AWS provisioning mode)", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    (getProvisioningLogs as any).mockResolvedValue({
      events: [
        { id: 1, phase: "pulumi_up", status: "log",
          message: "create ec2", created_at: "2026-05-25T00:00:00Z" },
      ],
      next_after: 1,
    });
  });
  afterEach(() => vi.useRealTimers());

  it("polls /provisioning-logs every 2s when provider=aws and state=provisioning", async () => {
    render(<NodeLogs nodeId="n1" nodeProvider="aws" nodeState="provisioning" />);
    await waitFor(() => expect(getProvisioningLogs).toHaveBeenCalledWith("n1", 0));
    expect(await screen.findByText(/create ec2/)).toBeInTheDocument();
    act(() => { vi.advanceTimersByTime(2000); });
    await waitFor(() => expect(getProvisioningLogs).toHaveBeenLastCalledWith("n1", 1));
  });

  it("does not poll provisioning when state='ready' (delegates to WS path)", async () => {
    render(<NodeLogs nodeId="n1" nodeProvider="aws" nodeState="ready" />);
    act(() => { vi.advanceTimersByTime(5000); });
    expect(getProvisioningLogs).not.toHaveBeenCalled();
  });

  it("fetches EC2 console when the user clicks the button", async () => {
    (getEC2Console as any).mockResolvedValue({
      logs: ["[boot] cloud-init"], fetched_at: "2026-05-25T00:00:00Z",
    });
    render(<NodeLogs nodeId="n1" nodeProvider="aws" nodeState="provisioning" />);
    const btn = await screen.findByRole("button", { name: /fetch ec2 console/i });
    await act(async () => { btn.click(); });
    expect(getEC2Console).toHaveBeenCalledWith("n1");
    await waitFor(() => expect(screen.getByText(/cloud-init/)).toBeInTheDocument());
  });
});
```

- [ ] **Step 2: Run, confirm failure**

```bash
cd /storage/intern/hooman/work/InferiaLLM/apps/dashboard
npx vitest run src/components/nodes/NodeLogs.test.tsx
```

Expected: NodeLogs prop signature mismatch (does not accept `nodeProvider`/`nodeState`).

- [ ] **Step 3: Modify `NodeLogs`**

Edit `apps/dashboard/src/components/nodes/NodeLogs.tsx`. Update the props:

```typescript
interface NodeLogsProps {
  nodeId: string;
  nodeProvider?: string;
  nodeState?: string;
}
```

Right under the props destructure, branch:

```typescript
export default function NodeLogs({ nodeId, nodeProvider, nodeState }: NodeLogsProps) {
  const isAwsProvisioning =
    nodeProvider === "aws" &&
    (nodeState === "provisioning" ||
     (nodeState === "terminated"));

  if (isAwsProvisioning) {
    return <AwsProvisioningLogs nodeId={nodeId} />;
  }
  // ... existing WS log code unchanged ...
}
```

Add the sub-component at the bottom of the file:

```typescript
import {
  getProvisioningLogs,
  getEC2Console,
  type ProvisioningEvent,
} from "@/services/provisioningService";

function AwsProvisioningLogs({ nodeId }: { nodeId: string }) {
  const [events, setEvents] = useState<ProvisioningEvent[]>([]);
  const [after, setAfter] = useState(0);
  const [consoleLogs, setConsoleLogs] = useState<string[] | null>(null);
  const [consoleLoading, setConsoleLoading] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const tick = async () => {
      try {
        const r = await getProvisioningLogs(nodeId, after);
        if (cancelled) return;
        if (r.events.length) {
          setEvents(prev => [...prev, ...r.events]);
          if (r.next_after != null) setAfter(r.next_after);
        }
      } catch { /* swallow */ }
    };
    void tick();
    const h = window.setInterval(() => void tick(), 2000);
    return () => { cancelled = true; window.clearInterval(h); };
  }, [nodeId, after]);

  const fetchConsole = async () => {
    setConsoleLoading(true);
    try {
      const c = await getEC2Console(nodeId);
      setConsoleLogs(c.logs);
    } finally { setConsoleLoading(false); }
  };

  return (
    <div className="space-y-3">
      <div className="flex justify-end">
        <button
          onClick={() => void fetchConsole()}
          disabled={consoleLoading}
          className="h-8 px-3 border rounded-md text-xs hover:bg-muted/50"
        >
          {consoleLoading ? "Fetching…" : "Fetch EC2 console"}
        </button>
      </div>
      {consoleLogs && (
        <details open className="rounded-md border bg-card p-3">
          <summary className="text-xs font-semibold cursor-pointer">
            EC2 console output ({consoleLogs.length} lines)
          </summary>
          <pre className="mt-2 text-[11px] font-mono whitespace-pre-wrap break-all max-h-72 overflow-auto">
            {consoleLogs.join("\n")}
          </pre>
        </details>
      )}
      <div className="rounded-md border bg-card font-mono text-[11px] p-3 max-h-96 overflow-auto">
        {events.length === 0 ? (
          <div className="text-muted-foreground">Waiting for events…</div>
        ) : (
          events.map(e => (
            <div key={e.id} className={cn(
              "py-0.5",
              e.status === "failed" && "text-red-500",
              e.phase === "cloud_init" && "text-muted-foreground",
            )}>
              <span className="opacity-60">[{e.phase}/{e.status}]</span>{" "}
              {e.message ?? ""}
            </div>
          ))
        )}
      </div>
    </div>
  );
}
```

(Imports `useState`, `useEffect`, `cn` are likely already in the file from the existing implementation. Add if not.)

- [ ] **Step 4: Run tests, confirm pass**

```bash
cd /storage/intern/hooman/work/InferiaLLM/apps/dashboard
npx vitest run src/components/nodes/NodeLogs.test.tsx
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add apps/dashboard/src/components/nodes/NodeLogs.tsx \
        apps/dashboard/src/components/nodes/NodeLogs.test.tsx
git -c user.signingkey=/home/ankit/.ssh/id_ed25519_gh commit -S \
  -m "dashboard/NodeLogs: surface AWS provisioning + EC2 console"
```

---

## Task 10: Frontend — `NodeShell` disabled state during provisioning

**Files:**
- Modify: `apps/dashboard/src/components/nodes/NodeShell.tsx`
- Create: `apps/dashboard/src/components/nodes/NodeShell.test.tsx`

- [ ] **Step 1: Write failing tests**

Create `NodeShell.test.tsx`:

```typescript
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import NodeShell from "./NodeShell";

describe("NodeShell", () => {
  it("shows disabled placeholder when state=provisioning", () => {
    render(<NodeShell nodeId="n1" nodeState="provisioning" currentPhase="pulumi_up" />);
    expect(screen.getByText(/shell available once the worker registers/i))
      .toBeInTheDocument();
    expect(screen.getByText(/pulumi_up/i)).toBeInTheDocument();
  });

  it("falls back to existing WS shell when state=ready", () => {
    render(<NodeShell nodeId="n1" nodeState="ready" />);
    // existing WS-backed shell renders a terminal-style container
    expect(screen.queryByText(/shell available once/i)).not.toBeInTheDocument();
  });

  it("disabled placeholder shows 'pending' when no current phase", () => {
    render(<NodeShell nodeId="n1" nodeState="provisioning" />);
    expect(screen.getByText(/pending/i)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run, confirm failure**

```bash
cd /storage/intern/hooman/work/InferiaLLM/apps/dashboard
npx vitest run src/components/nodes/NodeShell.test.tsx
```

Expected: prop signature error.

- [ ] **Step 3: Modify `NodeShell`**

Edit `apps/dashboard/src/components/nodes/NodeShell.tsx`:

```typescript
interface NodeShellProps {
  nodeId: string;
  nodeState?: string;
  currentPhase?: string | null;
}

export default function NodeShell({ nodeId, nodeState, currentPhase }: NodeShellProps) {
  if (nodeState && nodeState !== "ready") {
    return (
      <div className="rounded-md border bg-card p-6 text-sm text-muted-foreground">
        Shell available once the worker registers. Currently {currentPhase ?? "pending"}…
      </div>
    );
  }
  // ... existing implementation unchanged ...
}
```

- [ ] **Step 4: Run tests, confirm pass**

```bash
npx vitest run src/components/nodes/NodeShell.test.tsx
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add apps/dashboard/src/components/nodes/NodeShell.tsx \
        apps/dashboard/src/components/nodes/NodeShell.test.tsx
git -c user.signingkey=/home/ankit/.ssh/id_ed25519_gh commit -S \
  -m "dashboard/NodeShell: show placeholder during provisioning"
```

---

## Task 11: `InstanceDetail` — adaptive poll, tab visibility, ProvisioningStatus mount

**Files:**
- Modify: `apps/dashboard/src/pages/Compute/InstanceDetail.tsx`
- Create: `apps/dashboard/src/pages/Compute/InstanceDetail.test.tsx`

- [ ] **Step 1: Write failing tests**

Create `InstanceDetail.test.tsx`:

```typescript
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, act, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import InstanceDetail from "./InstanceDetail";

vi.mock("@/services/nodeService", () => ({
  getNode:      vi.fn(),
  patchLabels:  vi.fn(),
  deleteNode:   vi.fn(),
}));
vi.mock("@/services/provisioningService", () => ({
  getProvisioning: vi.fn(),
  ALL_PHASES: ["prepare","ami_lookup","pulumi_init","pulumi_up","ec2_running","cloud_init","worker_bootstrap","ready"],
}));
vi.mock("@/context/AuthContext", () => ({
  useAuth: () => ({ hasPermission: () => true }),
}));
vi.mock("@/components/nodes/NodeLogs", () => ({ default: () => <div>logs</div> }));
vi.mock("@/components/nodes/NodeShell", () => ({ default: () => <div>shell</div> }));

const { getNode } = await import("@/services/nodeService");
const { getProvisioning } = await import("@/services/provisioningService");

function renderAt(id: string) {
  return render(
    <MemoryRouter initialEntries={[`/dashboard/compute/nodes/${id}`]}>
      <Routes>
        <Route path="/dashboard/compute/nodes/:id" element={<InstanceDetail />} />
      </Routes>
    </MemoryRouter>,
  );
}

const baseNode = (overrides: any = {}) => ({
  id: "n1", node_name: "test", agent_kind: null, provider: "aws",
  state: "provisioning", labels: {}, advertise_url: null,
  gpu_total: 0, gpu_allocated: 0, vcpu_total: 0, vcpu_allocated: 0,
  last_heartbeat: null, ...overrides,
});

describe("InstanceDetail (AWS provisioning)", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    (getNode as any).mockResolvedValue(baseNode());
    (getProvisioning as any).mockResolvedValue({
      current_phase: "pulumi_up", terminal: false,
      phases: [
        { phase: "prepare", status: "succeeded", started_at: "x", ended_at: "y", last_message: null },
        { phase: "pulumi_up", status: "running",  started_at: "x", ended_at: null, last_message: "creating ec2" },
      ],
    });
  });
  afterEach(() => vi.useRealTimers());

  it("shows Logs and Shell tabs for aws provider even when state != ready", async () => {
    renderAt("n1");
    expect(await screen.findByRole("button", { name: /Logs/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Shell/i })).toBeInTheDocument();
  });

  it("renders ProvisioningStatus card on Overview when state=provisioning", async () => {
    renderAt("n1");
    await waitFor(() => expect(getProvisioning).toHaveBeenCalled());
    expect(screen.getByText(/Provisioning Status/i)).toBeInTheDocument();
  });

  it("polls /provisioning every 2s when state=provisioning", async () => {
    renderAt("n1");
    await waitFor(() => expect(getProvisioning).toHaveBeenCalledTimes(1));
    act(() => { vi.advanceTimersByTime(2000); });
    await waitFor(() => expect(getProvisioning).toHaveBeenCalledTimes(2));
  });

  it("polls every 15s when state=ready (no fast provisioning poll)", async () => {
    (getNode as any).mockResolvedValue(baseNode({ state: "ready", agent_kind: "worker" }));
    renderAt("n1");
    await waitFor(() => expect(getNode).toHaveBeenCalledTimes(1));
    act(() => { vi.advanceTimersByTime(2000); });
    expect(getNode).toHaveBeenCalledTimes(1);
    act(() => { vi.advanceTimersByTime(13000); });
    await waitFor(() => expect(getNode).toHaveBeenCalledTimes(2));
  });
});
```

- [ ] **Step 2: Run, confirm failure**

```bash
cd /storage/intern/hooman/work/InferiaLLM/apps/dashboard
npx vitest run src/pages/Compute/InstanceDetail.test.tsx
```

Expected: tab visibility test fails (logs/shell currently gated on `isWorker`).

- [ ] **Step 3: Modify `InstanceDetail.tsx`**

In the imports, add:

```typescript
import ProvisioningStatus from "@/components/nodes/ProvisioningStatus";
import { getProvisioning, type ProvisioningSummary } from "@/services/provisioningService";
```

Replace the `isWorker` derivation block:

```typescript
  const isWorker = node.agent_kind === "worker";
  const isAws = node.provider === "aws";
  const isProvisioning = node.state === "provisioning";
  const showLogsAndShell = isWorker || isAws;
```

Replace the existing tab-list expression:

```typescript
        {([
          { label: "Overview", value: "overview" as const, icon: null },
          { label: "Labels",   value: "labels"   as const, icon: Tag },
          ...(showLogsAndShell
            ? [
                { label: "Logs",  value: "logs"  as const, icon: ScrollText },
                { label: "Shell", value: "shell" as const, icon: Terminal },
              ]
            : []),
        ] as { label: string; value: Tab; icon: React.ComponentType<{ className?: string }> | null }[]).map((t) => ( ...
```

Add provisioning state next to `node` state:

```typescript
  const [provisioning, setProvisioning] = useState<ProvisioningSummary | null>(null);

  useEffect(() => {
    if (!id || !isAws) return;
    let cancelled = false;
    const tick = async () => {
      try {
        const r = await getProvisioning(id);
        if (!cancelled) setProvisioning(r);
      } catch { /* swallow */ }
    };
    void tick();
    const h = window.setInterval(() => void tick(), 2000);
    return () => { cancelled = true; window.clearInterval(h); };
  }, [id, isAws]);
```

Adapt the node-poll cadence:

```typescript
  useEffect(() => {
    void fetchNode();
    const period = (node?.state === "provisioning" || node?.state === "ordered") ? 2000 : 15000;
    const interval = window.setInterval(() => void fetchNode(true), period);
    return () => window.clearInterval(interval);
  }, [fetchNode, node?.state]);
```

Inject ProvisioningStatus into Overview:

```typescript
        {activeTab === "overview" && (
          <div className="grid grid-cols-1 gap-6">
            {isAws && provisioning && (isProvisioning || provisioning.phases.some(p => p.status === "failed")) && (
              <ProvisioningStatus summary={provisioning} />
            )}
            <div className="rounded-xl border bg-card text-card-foreground shadow-sm p-6">
              {/* existing Node Information card */}
              ...
```

Update logs/shell tab content to pass props:

```typescript
        {activeTab === "logs" && showLogsAndShell && (
          <div className="space-y-3">
            <div className="text-xs text-muted-foreground">
              {isAws && isProvisioning
                ? "Live Pulumi events and (manually-fetched) EC2 console output. Once the worker registers, switches to model-container logs."
                : "Live tail of the most recent model container managed by this worker."}
            </div>
            <NodeLogs nodeId={node.id} nodeProvider={node.provider ?? undefined} nodeState={node.state} />
          </div>
        )}

        {activeTab === "shell" && showLogsAndShell && (
          <div className="space-y-3">
            <NodeShell
              nodeId={node.id}
              nodeState={node.state}
              currentPhase={provisioning?.current_phase ?? null}
            />
          </div>
        )}
```

- [ ] **Step 4: Run tests, confirm pass**

```bash
npx vitest run src/pages/Compute/InstanceDetail.test.tsx
```

Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add apps/dashboard/src/pages/Compute/InstanceDetail.tsx \
        apps/dashboard/src/pages/Compute/InstanceDetail.test.tsx
git -c user.signingkey=/home/ankit/.ssh/id_ed25519_gh commit -S \
  -m "dashboard/InstanceDetail: AWS tabs, provisioning poll, status card"
```

---

## Task 12: `NewPool` — navigate to InstanceDetail after AWS createpool

**Files:**
- Modify: `apps/dashboard/src/pages/Compute/NewPool.tsx`

- [ ] **Step 1: Locate the post-createpool block**

Read `apps/dashboard/src/pages/Compute/NewPool.tsx` around line 508 (the `computeApi.post("/deployment/createpool", payload)` call). The current code likely navigates back to the nodes list. We change AWS to land on the instance detail.

- [ ] **Step 2: Update the success branch**

Right after the createpool POST succeeds, for `provider === "aws"`:

```typescript
const created = await computeApi.post("/deployment/createpool", payload);
const poolId: string = created.data.pool_id;

if (provider === "aws") {
  // Resolve the placeholder node id for the freshly-created pool. The
  // /createpool handler inserts it inline so a short retry suffices.
  let nodeId: string | null = null;
  for (let i = 0; i < 5 && !nodeId; i++) {
    try {
      const nodes = await listNodes();
      const match = nodes.find(n => n.pool_id === poolId);
      if (match) nodeId = match.id;
    } catch { /* swallow */ }
    if (!nodeId) await new Promise(r => setTimeout(r, 300));
  }
  if (nodeId) {
    navigate(`/dashboard/compute/nodes/${nodeId}?tab=overview`, { replace: true });
    return;
  }
}
navigate("/dashboard/compute/nodes", { replace: true });
```

Make sure `listNodes` is imported from `@/services/nodeService`.

- [ ] **Step 3: Smoke test manually**

Run the dashboard dev server:

```bash
cd /storage/intern/hooman/work/InferiaLLM/apps/dashboard
npm run dev
```

In another shell, run the stack with AWS creds set in the providers config. Open the dashboard, create an AWS compute node, and verify:
- Browser lands on `/dashboard/compute/nodes/<id>` immediately.
- Overview shows the ProvisioningStatus card.
- Phases advance from `prepare` to `pulumi_up` running.
- Logs tab shows Pulumi events.
- Shell tab shows the disabled placeholder.
- Once Pulumi up succeeds, `ec2_running` flips to succeeded; once the worker bootstraps and registers, `state='ready'`, Logs tab switches to the worker WS log stream, Shell tab enables.

If you can't run live AWS, mock `provision_node` in the orchestration container with a script that fires fake events into `node_provisioning_events` (insert rows via psql with progressive timestamps) and confirm the UI advances.

- [ ] **Step 4: Commit**

```bash
git add apps/dashboard/src/pages/Compute/NewPool.tsx
git -c user.signingkey=/home/ankit/.ssh/id_ed25519_gh commit -S \
  -m "dashboard/NewPool: jump to instance detail after AWS createpool"
```

---

## Task 13: Coverage check + integration smoke

- [ ] **Step 1: Backend coverage**

```bash
cd /storage/intern/hooman/work/InferiaLLM
PYTHONPATH=package/src python -m pytest \
  --cov=package/src/inferia/services/orchestration/repositories/node_provisioning_repo \
  --cov=package/src/inferia/services/orchestration/services/adapter_engine/adapters/pulumi/progress_writer \
  --cov=package/src/inferia/services/orchestration/services/adapter_engine/adapters/pulumi/pulumi_aws_adapter \
  --cov=package/src/inferia/services/orchestration/api/nodes \
  --cov-report=term-missing \
  package/src/inferia/services/orchestration/repositories/tests/test_node_provisioning_repo.py \
  package/src/inferia/services/orchestration/services/adapter_engine/adapters/pulumi/tests/ \
  package/src/inferia/services/orchestration/services/model_deployment/tests/test_createpool_aws_eager.py \
  package/src/inferia/services/orchestration/api/tests/test_nodes_provisioning_endpoints.py
```

Expected: each touched module ≥95% line coverage. If anything is under, add a test for the missing branch and re-run.

- [ ] **Step 2: Frontend coverage**

```bash
cd /storage/intern/hooman/work/InferiaLLM/apps/dashboard
npx vitest run --coverage \
  src/services/provisioningService.test.ts \
  src/components/nodes/ProvisioningStatus.test.tsx \
  src/components/nodes/NodeLogs.test.tsx \
  src/components/nodes/NodeShell.test.tsx \
  src/pages/Compute/InstanceDetail.test.tsx
```

Expected: ≥95% line coverage on the new files and the modified branches.

- [ ] **Step 3: End-to-end smoke against running stack**

With the stack up and AWS creds saved via the Settings → Providers UI:
1. Navigate to `/dashboard/compute/new-pool`. Select **AWS**, region, instance type, click **Create**.
2. Verify redirect to `/dashboard/compute/nodes/<id>?tab=overview`.
3. Verify ProvisioningStatus advances `prepare → pulumi_init → pulumi_up → ec2_running → cloud_init → worker_bootstrap → ready`.
4. Click **Logs** tab during provisioning; verify Pulumi event lines appear; click **Fetch EC2 console** and verify console output renders.
5. Click **Shell** tab; verify disabled placeholder mentions the current phase.
6. Once node state = `ready`, click **Logs** again; verify it switches to the worker WS stream.
7. Click **Shell**; verify the interactive shell works.

- [ ] **Step 4: Memory update**

Append a `feedback_aws_provisioning_ux.md` memory at `/home/ankit/.claude/projects/-storage-intern-hooman-work/memory/` documenting any surprising behavior surfaced during the smoke (e.g. how long `cloud_init` takes on first run, common Pulumi event noise). Update `MEMORY.md` index.

- [ ] **Step 5: Final commit + PR**

```bash
cd /storage/intern/hooman/work/InferiaLLM
git log --oneline main..HEAD     # review the commit chain
# Push to a feature branch and open a PR; do NOT push directly to main.
git push -u origin aws-provisioning-ux
gh pr create --title "AWS compute node provisioning UX" --body "$(cat <<'EOF'
## Summary
- Eagerly provision EC2 instance when an AWS compute node is created from the dashboard.
- Surface 8-phase progress in the InstanceDetail Overview tab.
- Logs tab streams Pulumi events + EC2 console output during provisioning; switches to worker WS once the worker registers.
- Shell tab disabled with phase-aware placeholder until the worker registers.

## Test plan
- [ ] Backend pytest suite passes; ≥95% line coverage on touched modules.
- [ ] Frontend vitest suite passes; ≥95% line coverage on touched components.
- [ ] End-to-end smoke against live AWS creds — full ready transition.
- [ ] Regression: nosana/akash createpool path unchanged (placeholder=ready, no provision_node call).
EOF
)"
```

---

## Self-Review Notes

**Spec coverage:** All 8 phases written (Task 4 + Task 8 list). New table created (Task 1), repo (Task 2), writer (Task 3), adapter integration (Task 4), createpool change (Task 5), REST endpoints (Task 6), frontend service + components + page + nav (Tasks 7-12). EC2 console fetch surfaced (Task 9). Tab visibility, adaptive poll, fast/slow handover (Task 11). ≥95% coverage requirement (Task 13).

**Type consistency check:**
- `ProgressWriter.write` / `write_async` signatures (Task 3) used identically in adapter (Task 4) and createpool (Task 5).
- `PHASES` tuple in repo (Task 2) matches `ALL_PHASES` in frontend service (Task 7) — both 8 entries, same order, same names.
- `provisioning_repo` keyword in `nodes_api.configure` (Task 6) is used in `server.py` (Task 6) and in test fixture (Task 6 tests).
- `NodeLogs` props `nodeProvider`/`nodeState` (Task 9) match the call site in `InstanceDetail` (Task 11).
- `NodeShell` props `nodeState`/`currentPhase` (Task 10) match Task 11.

**Placeholder scan:** No "TBD", no "implement later", no "similar to". Every code step includes the actual code.

**Edge cases covered in tests:**
- Empty pool (Task 2 test 4), cursor exhaustion (Task 2 test 3), log-status ignored in summary (Task 2 test 7), unknown pool (Task 2 test 8), terminal=true detection (Task 2 test 10).
- ProgressWriter: cross-thread dispatch (Task 3 test 3), repo error swallowed (Task 3 test 4), message truncation (Task 3 test 5), None message (Task 3 test 6).
- Adapter: AMI pinned vs lookup (Task 4 tests 1 + 2), Pulumi up failure (Task 4 test 3), no-writer compat (Task 4 test 4), on_event wired (Task 4 test 5).
- createpool: AWS path provisions eagerly (Task 5 test 1), nosana path unchanged (Task 5 test 2).
- Endpoints: 404 on missing node (Task 6 test 5), 404 on non-AWS for `/ec2-console` (Task 6 test 6), terminal detection (Task 6 test 8).
- Frontend: tab visibility (Task 11 test 1), fast vs slow poll (Task 11 tests 3+4), ProvisioningStatus icons (Task 8 tests 2-5).

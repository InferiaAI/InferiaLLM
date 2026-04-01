# Deployment Terminal Log Persistence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist deployment terminal logs to Elasticsearch so they survive deployment failures/stops and can be viewed in the dashboard after the fact.

**Architecture:** Sniff log messages from the existing WebSocket relay in `deployment_server.py`, buffer them in a 10k-line circular deque, and flush to ES every 10 seconds + on disconnect. The existing `/deployment/logs/{deployment_id}` endpoint falls back to ES when the provider can't serve live logs. The frontend detects terminated/failed deployments and loads persisted logs.

**Tech Stack:** Python (`elasticsearch[async]`), FastAPI, React/TypeScript

---

## File Structure

| File | Responsibility |
|------|---------------|
| **Create:** `package/src/inferia/services/orchestration/services/model_deployment/log_store.py` | `DeploymentLogStore` (async ES client wrapper) and `DeploymentLogBuffer` (circular buffer + periodic flush) |
| **Create:** `package/src/inferia/services/orchestration/test/model_deployment/test_log_store.py` | Unit tests for `DeploymentLogStore` and `DeploymentLogBuffer` |
| **Modify:** `package/src/inferia/services/orchestration/config.py` | Add ES URL, buffer size, flush interval settings |
| **Modify:** `package/src/inferia/services/orchestration/services/model_deployment/deployment_server.py` | Hook buffer into WS relay loops, add ES fallback in `get_deployment_logs()` |
| **Modify:** `apps/dashboard/src/components/deployment/TerminalLogs.tsx` | Load persisted logs when live stream unavailable, show "Saved logs" indicator |
| **Modify:** `package/pyproject.toml` | Add `elasticsearch[async]` optional dependency |

---

### Task 1: Add Elasticsearch optional dependency

**Files:**
- Modify: `package/pyproject.toml:100-102`

- [ ] **Step 1: Add elasticsearch optional dependency group**

In `package/pyproject.toml`, add a new optional dependency group after the `logstash` group:

```toml
elasticsearch = [
  "elasticsearch[async]>=8.0,<9.0",
]
```

Also add it to the `logstash` group since they go together:

```toml
logstash = [
  "python-logstash>=0.4.6",
  "elasticsearch[async]>=8.0,<9.0",
]
```

- [ ] **Step 2: Install the dependency**

Run: `cd /storage/intern/hooman/InferiaLLM/package && pip install -e ".[elasticsearch]"`
Expected: Successful installation of `elasticsearch` package

- [ ] **Step 3: Commit**

```bash
git add package/pyproject.toml
git commit -m "feat: add elasticsearch[async] optional dependency for log persistence (#169)"
```

---

### Task 2: Add config settings for deployment log persistence

**Files:**
- Modify: `package/src/inferia/services/orchestration/config.py:58-174`

- [ ] **Step 1: Write the failing test**

Create `package/src/inferia/services/orchestration/test/model_deployment/test_log_store.py`:

```python
"""Tests for deployment log persistence."""

import pytest
from inferia.services.orchestration.config import Settings


class TestLogPersistenceConfig:
    def test_default_elasticsearch_url_is_none(self):
        s = Settings(
            _env_file=None,
            postgres_dsn="postgresql://test:test@localhost/test",
        )
        assert s.elasticsearch_url is None

    def test_default_buffer_size(self):
        s = Settings(
            _env_file=None,
            postgres_dsn="postgresql://test:test@localhost/test",
        )
        assert s.deployment_log_buffer_size == 10000

    def test_default_flush_interval(self):
        s = Settings(
            _env_file=None,
            postgres_dsn="postgresql://test:test@localhost/test",
        )
        assert s.deployment_log_flush_interval == 10
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /storage/intern/hooman/InferiaLLM && python -m pytest package/src/inferia/services/orchestration/test/model_deployment/test_log_store.py::TestLogPersistenceConfig -v`
Expected: FAIL with `AttributeError` — fields don't exist yet

- [ ] **Step 3: Add config fields to Settings**

In `package/src/inferia/services/orchestration/config.py`, add these fields to the `Settings` class after the `ephemeral_failure_threshold_minutes` field (after line 107):

```python
    # Deployment Log Persistence (Elasticsearch)
    elasticsearch_url: Optional[str] = Field(
        default=None, validation_alias="ELASTICSEARCH_URL"
    )
    deployment_log_buffer_size: int = Field(
        default=10000, validation_alias="DEPLOYMENT_LOG_BUFFER_SIZE"
    )
    deployment_log_flush_interval: int = Field(
        default=10, validation_alias="DEPLOYMENT_LOG_FLUSH_INTERVAL"
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /storage/intern/hooman/InferiaLLM && python -m pytest package/src/inferia/services/orchestration/test/model_deployment/test_log_store.py::TestLogPersistenceConfig -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add package/src/inferia/services/orchestration/config.py package/src/inferia/services/orchestration/test/model_deployment/test_log_store.py
git commit -m "feat: add elasticsearch config fields for deployment log persistence (#169)"
```

---

### Task 3: Implement DeploymentLogStore (ES client wrapper)

**Files:**
- Create: `package/src/inferia/services/orchestration/services/model_deployment/log_store.py`
- Modify: `package/src/inferia/services/orchestration/test/model_deployment/test_log_store.py`

- [ ] **Step 1: Write the failing tests for DeploymentLogStore**

Append to `test_log_store.py`:

```python
from unittest.mock import AsyncMock, MagicMock, patch
from collections import deque
from datetime import datetime, timezone


class TestDeploymentLogStore:
    @pytest.mark.asyncio
    async def test_init_sets_unavailable_when_no_url(self):
        from inferia.services.orchestration.services.model_deployment.log_store import (
            DeploymentLogStore,
        )

        store = DeploymentLogStore(elasticsearch_url=None)
        await store.initialize()
        assert store.available is False

    @pytest.mark.asyncio
    async def test_init_sets_available_when_es_responds(self):
        from inferia.services.orchestration.services.model_deployment.log_store import (
            DeploymentLogStore,
        )

        with patch(
            "inferia.services.orchestration.services.model_deployment.log_store.AsyncElasticsearch"
        ) as MockES:
            mock_client = AsyncMock()
            mock_client.ping = AsyncMock(return_value=True)
            MockES.return_value = mock_client

            store = DeploymentLogStore(elasticsearch_url="http://localhost:9200")
            await store.initialize()
            assert store.available is True

    @pytest.mark.asyncio
    async def test_init_sets_unavailable_when_es_unreachable(self):
        from inferia.services.orchestration.services.model_deployment.log_store import (
            DeploymentLogStore,
        )

        with patch(
            "inferia.services.orchestration.services.model_deployment.log_store.AsyncElasticsearch"
        ) as MockES:
            mock_client = AsyncMock()
            mock_client.ping = AsyncMock(side_effect=Exception("connection refused"))
            MockES.return_value = mock_client

            store = DeploymentLogStore(elasticsearch_url="http://localhost:9200")
            await store.initialize()
            assert store.available is False

    @pytest.mark.asyncio
    async def test_flush_is_noop_when_unavailable(self):
        from inferia.services.orchestration.services.model_deployment.log_store import (
            DeploymentLogStore,
        )

        store = DeploymentLogStore(elasticsearch_url=None)
        await store.initialize()
        # Should not raise
        await store.flush("dep-1", "org-1", [("msg1", 1, datetime.now(timezone.utc))])

    @pytest.mark.asyncio
    async def test_flush_bulk_indexes_lines(self):
        from inferia.services.orchestration.services.model_deployment.log_store import (
            DeploymentLogStore,
        )

        with patch(
            "inferia.services.orchestration.services.model_deployment.log_store.AsyncElasticsearch"
        ) as MockES:
            mock_client = AsyncMock()
            mock_client.ping = AsyncMock(return_value=True)
            mock_client.bulk = AsyncMock(return_value={"errors": False})
            MockES.return_value = mock_client

            store = DeploymentLogStore(elasticsearch_url="http://localhost:9200")
            await store.initialize()

            now = datetime.now(timezone.utc)
            lines = [
                ("line one", 1, now),
                ("line two", 2, now),
            ]
            await store.flush("dep-123", "org-456", lines)

            mock_client.bulk.assert_called_once()
            call_args = mock_client.bulk.call_args
            operations = call_args.kwargs.get("operations") or call_args.args[0]
            # 2 lines = 4 bulk entries (action + doc pairs)
            assert len(operations) == 4

    @pytest.mark.asyncio
    async def test_get_logs_returns_empty_when_unavailable(self):
        from inferia.services.orchestration.services.model_deployment.log_store import (
            DeploymentLogStore,
        )

        store = DeploymentLogStore(elasticsearch_url=None)
        await store.initialize()
        result = await store.get_logs("dep-1")
        assert result == []

    @pytest.mark.asyncio
    async def test_get_logs_queries_es_by_deployment_id(self):
        from inferia.services.orchestration.services.model_deployment.log_store import (
            DeploymentLogStore,
        )

        with patch(
            "inferia.services.orchestration.services.model_deployment.log_store.AsyncElasticsearch"
        ) as MockES:
            mock_client = AsyncMock()
            mock_client.ping = AsyncMock(return_value=True)
            mock_client.search = AsyncMock(
                return_value={
                    "hits": {
                        "hits": [
                            {
                                "_source": {
                                    "message": "hello",
                                    "line_number": 1,
                                    "timestamp": "2026-04-01T00:00:00Z",
                                }
                            },
                            {
                                "_source": {
                                    "message": "world",
                                    "line_number": 2,
                                    "timestamp": "2026-04-01T00:00:01Z",
                                }
                            },
                        ]
                    }
                }
            )
            MockES.return_value = mock_client

            store = DeploymentLogStore(elasticsearch_url="http://localhost:9200")
            await store.initialize()
            result = await store.get_logs("dep-123")
            assert result == ["hello", "world"]

    @pytest.mark.asyncio
    async def test_get_max_line_number_returns_zero_when_no_docs(self):
        from inferia.services.orchestration.services.model_deployment.log_store import (
            DeploymentLogStore,
        )

        with patch(
            "inferia.services.orchestration.services.model_deployment.log_store.AsyncElasticsearch"
        ) as MockES:
            mock_client = AsyncMock()
            mock_client.ping = AsyncMock(return_value=True)
            mock_client.search = AsyncMock(
                return_value={"hits": {"hits": []}}
            )
            MockES.return_value = mock_client

            store = DeploymentLogStore(elasticsearch_url="http://localhost:9200")
            await store.initialize()
            result = await store.get_max_line_number("dep-123")
            assert result == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /storage/intern/hooman/InferiaLLM && python -m pytest package/src/inferia/services/orchestration/test/model_deployment/test_log_store.py::TestDeploymentLogStore -v`
Expected: FAIL with `ImportError` — `log_store` module doesn't exist yet

- [ ] **Step 3: Implement DeploymentLogStore**

Create `package/src/inferia/services/orchestration/services/model_deployment/log_store.py`:

```python
"""
Deployment terminal log persistence via Elasticsearch.

Provides DeploymentLogStore (ES client wrapper) and DeploymentLogBuffer
(circular buffer with periodic flush) for persisting terminal logs that
are sniffed from the WebSocket relay.
"""

import logging
from datetime import datetime, timezone
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)


def _get_index_name() -> str:
    """Return the ES index name for today's deployment logs."""
    return f"inferia-deployment-logs-{datetime.now(timezone.utc).strftime('%Y.%m.%d')}"


class DeploymentLogStore:
    """Async Elasticsearch client wrapper for deployment terminal logs."""

    def __init__(self, elasticsearch_url: Optional[str] = None):
        self._url = elasticsearch_url
        self._client = None
        self.available = False

    async def initialize(self) -> None:
        """Ping ES and set availability flag. Safe to call multiple times."""
        if not self._url:
            self.available = False
            return

        try:
            from elasticsearch import AsyncElasticsearch

            self._client = AsyncElasticsearch(self._url)
            if await self._client.ping():
                self.available = True
                logger.info("Deployment log store connected to Elasticsearch")
            else:
                self.available = False
                logger.warning("Elasticsearch ping failed — deployment log persistence disabled")
        except Exception as e:
            self.available = False
            logger.warning(f"Elasticsearch unavailable — deployment log persistence disabled: {e}")

    async def flush(
        self,
        deployment_id: str,
        org_id: str,
        lines: List[Tuple[str, int, datetime]],
    ) -> None:
        """
        Bulk-index a batch of log lines to ES.

        Args:
            deployment_id: The deployment UUID string.
            org_id: The organization ID.
            lines: List of (message, line_number, timestamp) tuples.
        """
        if not self.available or not lines:
            return

        index = _get_index_name()
        operations = []
        for message, line_number, timestamp in lines:
            doc_id = f"{deployment_id}-{line_number}"
            operations.append({"index": {"_index": index, "_id": doc_id}})
            operations.append(
                {
                    "deployment_id": deployment_id,
                    "org_id": org_id,
                    "line_number": line_number,
                    "message": message,
                    "timestamp": timestamp.isoformat(),
                }
            )

        try:
            result = await self._client.bulk(operations=operations)
            if result.get("errors"):
                logger.warning(f"Some deployment log lines failed to index for {deployment_id}")
        except Exception as e:
            logger.warning(f"Failed to flush deployment logs to ES: {e}")

    async def get_logs(self, deployment_id: str, limit: int = 10000) -> List[str]:
        """
        Retrieve persisted log lines for a deployment, sorted by line_number.

        Returns:
            List of log message strings.
        """
        if not self.available:
            return []

        try:
            result = await self._client.search(
                index="inferia-deployment-logs-*",
                query={"term": {"deployment_id": deployment_id}},
                sort=[{"line_number": "asc"}],
                size=limit,
                _source=["message", "line_number", "timestamp"],
            )
            return [hit["_source"]["message"] for hit in result["hits"]["hits"]]
        except Exception as e:
            logger.warning(f"Failed to query deployment logs from ES: {e}")
            return []

    async def get_max_line_number(self, deployment_id: str) -> int:
        """
        Get the highest line_number stored in ES for a deployment.
        Used to initialize the buffer's sequence counter for idempotent upserts.
        """
        if not self.available:
            return 0

        try:
            result = await self._client.search(
                index="inferia-deployment-logs-*",
                query={"term": {"deployment_id": deployment_id}},
                sort=[{"line_number": "desc"}],
                size=1,
                _source=["line_number"],
            )
            hits = result["hits"]["hits"]
            if hits:
                return hits[0]["_source"]["line_number"]
            return 0
        except Exception:
            return 0

    async def close(self) -> None:
        """Close the ES client connection."""
        if self._client:
            await self._client.close()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /storage/intern/hooman/InferiaLLM && python -m pytest package/src/inferia/services/orchestration/test/model_deployment/test_log_store.py::TestDeploymentLogStore -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Commit**

```bash
git add package/src/inferia/services/orchestration/services/model_deployment/log_store.py package/src/inferia/services/orchestration/test/model_deployment/test_log_store.py
git commit -m "feat: implement DeploymentLogStore ES client wrapper (#169)"
```

---

### Task 4: Implement DeploymentLogBuffer (circular buffer + periodic flush)

**Files:**
- Modify: `package/src/inferia/services/orchestration/services/model_deployment/log_store.py`
- Modify: `package/src/inferia/services/orchestration/test/model_deployment/test_log_store.py`

- [ ] **Step 1: Write the failing tests for DeploymentLogBuffer**

Append to `test_log_store.py`:

```python
import asyncio


class TestDeploymentLogBuffer:
    @pytest.mark.asyncio
    async def test_append_adds_to_buffer(self):
        from inferia.services.orchestration.services.model_deployment.log_store import (
            DeploymentLogBuffer,
            DeploymentLogStore,
        )

        store = DeploymentLogStore(elasticsearch_url=None)
        await store.initialize()

        buf = DeploymentLogBuffer(
            store=store,
            deployment_id="dep-1",
            org_id="org-1",
            max_lines=100,
            flush_interval=60,
        )
        buf.append("hello")
        buf.append("world")
        assert len(buf._buffer) == 2

    @pytest.mark.asyncio
    async def test_buffer_respects_max_lines(self):
        from inferia.services.orchestration.services.model_deployment.log_store import (
            DeploymentLogBuffer,
            DeploymentLogStore,
        )

        store = DeploymentLogStore(elasticsearch_url=None)
        await store.initialize()

        buf = DeploymentLogBuffer(
            store=store,
            deployment_id="dep-1",
            org_id="org-1",
            max_lines=3,
            flush_interval=60,
        )
        for i in range(5):
            buf.append(f"line {i}")

        # deque maxlen=3, oldest dropped
        assert len(buf._buffer) == 3
        messages = [msg for msg, _, _ in buf._buffer]
        assert messages == ["line 2", "line 3", "line 4"]

    @pytest.mark.asyncio
    async def test_flush_sends_pending_lines_to_store(self):
        from inferia.services.orchestration.services.model_deployment.log_store import (
            DeploymentLogBuffer,
            DeploymentLogStore,
        )

        with patch(
            "inferia.services.orchestration.services.model_deployment.log_store.AsyncElasticsearch"
        ) as MockES:
            mock_client = AsyncMock()
            mock_client.ping = AsyncMock(return_value=True)
            mock_client.bulk = AsyncMock(return_value={"errors": False})
            mock_client.search = AsyncMock(return_value={"hits": {"hits": []}})
            MockES.return_value = mock_client

            store = DeploymentLogStore(elasticsearch_url="http://localhost:9200")
            await store.initialize()

            buf = DeploymentLogBuffer(
                store=store,
                deployment_id="dep-1",
                org_id="org-1",
                max_lines=100,
                flush_interval=60,
            )
            buf.append("line A")
            buf.append("line B")

            await buf.flush()

            mock_client.bulk.assert_called_once()
            # After flush, pending list is cleared but buffer retains lines
            assert buf._flush_cursor == 2

    @pytest.mark.asyncio
    async def test_flush_is_noop_when_no_new_lines(self):
        from inferia.services.orchestration.services.model_deployment.log_store import (
            DeploymentLogBuffer,
            DeploymentLogStore,
        )

        store = DeploymentLogStore(elasticsearch_url=None)
        await store.initialize()

        buf = DeploymentLogBuffer(
            store=store,
            deployment_id="dep-1",
            org_id="org-1",
            max_lines=100,
            flush_interval=60,
        )
        # Should not raise
        await buf.flush()

    @pytest.mark.asyncio
    async def test_line_numbers_are_monotonic(self):
        from inferia.services.orchestration.services.model_deployment.log_store import (
            DeploymentLogBuffer,
            DeploymentLogStore,
        )

        store = DeploymentLogStore(elasticsearch_url=None)
        await store.initialize()

        buf = DeploymentLogBuffer(
            store=store,
            deployment_id="dep-1",
            org_id="org-1",
            max_lines=100,
            flush_interval=60,
            start_line_number=5,
        )
        buf.append("a")
        buf.append("b")
        buf.append("c")
        line_numbers = [ln for _, ln, _ in buf._buffer]
        assert line_numbers == [6, 7, 8]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /storage/intern/hooman/InferiaLLM && python -m pytest package/src/inferia/services/orchestration/test/model_deployment/test_log_store.py::TestDeploymentLogBuffer -v`
Expected: FAIL with `ImportError` — `DeploymentLogBuffer` doesn't exist yet

- [ ] **Step 3: Implement DeploymentLogBuffer**

Add to the bottom of `log_store.py`:

```python
from collections import deque


class DeploymentLogBuffer:
    """
    Circular buffer that accumulates log lines and periodically flushes
    them to Elasticsearch via DeploymentLogStore.
    """

    def __init__(
        self,
        store: DeploymentLogStore,
        deployment_id: str,
        org_id: str,
        max_lines: int = 10000,
        flush_interval: int = 10,
        start_line_number: int = 0,
    ):
        self._store = store
        self._deployment_id = deployment_id
        self._org_id = org_id
        self._buffer: deque[Tuple[str, int, datetime]] = deque(maxlen=max_lines)
        self._flush_interval = flush_interval
        self._next_line_number = start_line_number + 1
        self._flush_cursor = 0  # index into _buffer of next unflushed entry
        self._flush_task: Optional[asyncio.Task] = None

    def append(self, message: str) -> None:
        """Add a log line to the buffer."""
        self._buffer.append(
            (message, self._next_line_number, datetime.now(timezone.utc))
        )
        self._next_line_number += 1

    async def flush(self) -> None:
        """Flush any lines not yet sent to ES."""
        buf_list = list(self._buffer)
        pending = buf_list[self._flush_cursor :]
        if not pending:
            return
        await self._store.flush(self._deployment_id, self._org_id, pending)
        self._flush_cursor = len(buf_list)

    async def start_periodic_flush(self) -> None:
        """Start a background task that flushes every flush_interval seconds."""

        async def _loop():
            try:
                while True:
                    await asyncio.sleep(self._flush_interval)
                    await self.flush()
            except asyncio.CancelledError:
                pass

        self._flush_task = asyncio.create_task(_loop())

    async def stop(self) -> None:
        """Cancel periodic flush and do a final flush."""
        if self._flush_task:
            self._flush_task.cancel()
            try:
                await self._flush_task
            except asyncio.CancelledError:
                pass
        await self.flush()
```

Also add `import asyncio` to the top of the file imports.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /storage/intern/hooman/InferiaLLM && python -m pytest package/src/inferia/services/orchestration/test/model_deployment/test_log_store.py::TestDeploymentLogBuffer -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Run all log_store tests together**

Run: `cd /storage/intern/hooman/InferiaLLM && python -m pytest package/src/inferia/services/orchestration/test/model_deployment/test_log_store.py -v`
Expected: PASS (all 16 tests)

- [ ] **Step 6: Commit**

```bash
git add package/src/inferia/services/orchestration/services/model_deployment/log_store.py package/src/inferia/services/orchestration/test/model_deployment/test_log_store.py
git commit -m "feat: implement DeploymentLogBuffer with circular deque and periodic flush (#169)"
```

---

### Task 5: Hook buffer into WebSocket relay (deployment_server.py)

**Files:**
- Modify: `package/src/inferia/services/orchestration/services/model_deployment/deployment_server.py:1681-1912`

- [ ] **Step 1: Add log store singleton initialization**

At the top of `deployment_server.py`, after the existing imports (around line 28), add:

```python
from inferia.services.orchestration.services.model_deployment.log_store import (
    DeploymentLogStore,
    DeploymentLogBuffer,
)
from inferia.services.orchestration.config import settings

# Singleton log store — initialized lazily on first use
_log_store: Optional[DeploymentLogStore] = None


async def _get_log_store() -> DeploymentLogStore:
    """Get or initialize the deployment log store singleton."""
    global _log_store
    if _log_store is None:
        _log_store = DeploymentLogStore(
            elasticsearch_url=settings.elasticsearch_url
        )
        await _log_store.initialize()
    return _log_store
```

Also add `from typing import Optional` if not already imported.

- [ ] **Step 2: Create a helper to build a buffer for a WS session**

Below `_get_log_store()`, add:

```python
async def _create_log_buffer(deployment_id: str, org_id: str) -> DeploymentLogBuffer:
    """Create a log buffer for a WebSocket session, seeded with ES line count."""
    store = await _get_log_store()
    start_line = await store.get_max_line_number(deployment_id)
    return DeploymentLogBuffer(
        store=store,
        deployment_id=deployment_id,
        org_id=org_id,
        max_lines=settings.deployment_log_buffer_size,
        flush_interval=settings.deployment_log_flush_interval,
        start_line_number=start_line,
    )
```

- [ ] **Step 3: Hook buffer into the SkyPilot branch of websocket_logs_endpoint**

In the `websocket_logs_endpoint` function, in the `if provider == "skypilot":` branch, after `stream_task = asyncio.create_task(read_logs())` (line 1753), the `read_logs` inner function sends each line to the client. Modify it to also feed the buffer.

Replace the `read_logs` function and the code after it (lines 1739-1761) with:

```python
            # Create log buffer for persistence
            log_buffer = await _create_log_buffer(deployment_id=data.get("deployment_id", "unknown"), org_id=data.get("org_id", ""))
            await log_buffer.start_periodic_flush()

            async def read_logs():
                try:
                    while True:
                        line = await process.stdout.readline()
                        if not line:
                            break
                        decoded = line.decode().strip()
                        log_buffer.append(decoded)
                        await websocket.send_json(
                            {"type": "log", "data": decoded}
                        )
                except asyncio.CancelledError:
                    pass
                except Exception as e:
                    logger.error(f"Error reading logs: {e}")

            stream_task = asyncio.create_task(read_logs())

            try:
                # Wait for client to close or process to end
                while True:
                    try:
                        await websocket.receive_text()
                    except WebSocketDisconnect:
                        break
            finally:
                await log_buffer.stop()
```

- [ ] **Step 4: Hook buffer into the Nosana branch of websocket_logs_endpoint**

In the Nosana branch, the `sidecar_to_client` inner function relays messages. We need to also feed the buffer. The subscription message already contains `jobId` which we can use.

Modify the Nosana relay section (lines 1812-1884). After establishing the `sidecar_ws` connection and before defining `client_to_sidecar`, create the buffer:

```python
            try:
                async with websockets.connect(
                    ws_url,
                    additional_headers=headers,
                ) as sidecar_ws:
                    await sidecar_ws.send(json.dumps(subscribe_msg))
                    logger.info("Connected to Nosana node, relaying logs...")

                    # Create log buffer for persistence
                    log_buffer = await _create_log_buffer(
                        deployment_id=data.get("deployment_id", job_id),
                        org_id=data.get("org_id", ""),
                    )
                    await log_buffer.start_periodic_flush()

                    async def client_to_sidecar():
                        while True:
                            payload = await websocket.receive()
                            event_type = payload.get("type")
                            if event_type == "websocket.disconnect":
                                break
                            if payload.get("text"):
                                await sidecar_ws.send(payload["text"])

                    async def sidecar_to_client():
                        async for msg in sidecar_ws:
                            if isinstance(msg, bytes):
                                decoded = msg.decode("utf-8", errors="replace")
                                log_buffer.append(decoded)
                                await websocket.send_json(
                                    {"type": "log", "data": decoded}
                                )
                            else:
                                try:
                                    parsed = json.loads(msg)
                                    if isinstance(parsed, dict) and "data" in parsed:
                                        log_data = parsed["data"]
                                    else:
                                        log_data = msg
                                except json.JSONDecodeError:
                                    log_data = msg
                                log_buffer.append(str(log_data))
                                await websocket.send_json(
                                    {"type": "log", "data": log_data}
                                )

                    tasks = {
                        asyncio.create_task(client_to_sidecar()),
                        asyncio.create_task(sidecar_to_client()),
                    }
                    try:
                        done, pending = await asyncio.wait(
                            tasks, return_when=asyncio.FIRST_COMPLETED
                        )
                        for task in pending:
                            task.cancel()
                        for task in done:
                            exc = task.exception()
                            if exc:
                                logger.error(f"Nosana WS task error: {exc}")
                    finally:
                        await log_buffer.stop()
```

- [ ] **Step 5: Pass deployment_id and org_id in the subscription message from frontend**

The buffer needs `deployment_id` to persist logs. The subscription message from the frontend already flows through the WS. We need to ensure `deployment_id` is included in the subscription data sent by the frontend.

In `apps/dashboard/src/components/deployment/TerminalLogs.tsx`, the subscription object comes from the backend's `/stream` endpoint response. The backend already knows the deployment_id. Modify the backend's `get_deployment_log_stream_info` endpoint to include `deployment_id` in the subscription payload.

In `deployment_server.py`, in `get_deployment_log_stream_info()` (around line 1465-1468), modify to inject deployment_id into the subscription:

```python
            stream_info = await adapter.get_log_streaming_info(
                provider_instance_id=provider_instance_id, **extra_args
            )
            # Inject deployment_id into subscription for log persistence
            if isinstance(stream_info, dict) and "subscription" in stream_info:
                stream_info["subscription"]["deployment_id"] = deployment_id
            elif isinstance(stream_info, dict):
                stream_info["deployment_id"] = deployment_id
            return stream_info
```

We also need to get `org_id` from the deployment. Update the SQL query in `get_deployment_log_stream_info` to also fetch `org_id`:

Change the SQL at line 1416 from:
```sql
SELECT p.provider, p.provider_credential_name, d.node_ids
```
to:
```sql
SELECT p.provider, p.provider_credential_name, d.node_ids, d.org_id
```

And inject it similarly:
```python
            if isinstance(stream_info, dict) and "subscription" in stream_info:
                stream_info["subscription"]["deployment_id"] = deployment_id
                stream_info["subscription"]["org_id"] = dep.get("org_id", "")
```

- [ ] **Step 6: Verify existing tests still pass**

Run: `cd /storage/intern/hooman/InferiaLLM && python -m pytest package/src/inferia/services/orchestration/test/model_deployment/ -v`
Expected: All existing tests pass

- [ ] **Step 7: Commit**

```bash
git add package/src/inferia/services/orchestration/services/model_deployment/deployment_server.py
git commit -m "feat: hook log buffer into WebSocket relay for log persistence (#169)"
```

---

### Task 6: Add ES fallback to get_deployment_logs endpoint

**Files:**
- Modify: `package/src/inferia/services/orchestration/services/model_deployment/deployment_server.py:1317-1397`

- [ ] **Step 1: Add ES fallback in the except/error paths**

In the `get_deployment_logs` endpoint, the adapter's `get_logs()` may fail or return empty results (especially after deployment termination when the provider is gone). Add a fallback to ES.

Replace the adapter call section (lines 1381-1393) with:

```python
        # 3. Try adapter first, fall back to ES
        try:
            adapter = get_adapter(provider)
            if hasattr(adapter, "get_logs"):
                logs_data = await adapter.get_logs(
                    provider_instance_id=provider_instance_id
                )
                if logs_data and logs_data.get("logs"):
                    return logs_data
        except Exception as e:
            logger.warning(f"Adapter log fetch failed, trying ES fallback: {e}")

        # 4. Fallback: try Elasticsearch persisted logs
        try:
            store = await _get_log_store()
            es_logs = await store.get_logs(deployment_id)
            if es_logs:
                return {"logs": es_logs, "source": "persisted"}
        except Exception as e:
            logger.warning(f"ES log fallback also failed: {e}")

        return {"logs": [f"No logs available for provider: {provider}"], "source": "none"}
```

- [ ] **Step 2: Verify existing tests still pass**

Run: `cd /storage/intern/hooman/InferiaLLM && python -m pytest package/src/inferia/services/orchestration/test/model_deployment/ -v`
Expected: All tests pass

- [ ] **Step 3: Commit**

```bash
git add package/src/inferia/services/orchestration/services/model_deployment/deployment_server.py
git commit -m "feat: add ES fallback in get_deployment_logs endpoint (#169)"
```

---

### Task 7: Frontend — load persisted logs when live stream unavailable

**Files:**
- Modify: `apps/dashboard/src/components/deployment/TerminalLogs.tsx`

- [ ] **Step 1: Add a fetchPersistedLogs function**

In `TerminalLogs.tsx`, add a new state variable and function after the existing state declarations (after line 19):

```typescript
const [logSource, setLogSource] = useState<"live" | "persisted" | null>(null)
```

Add a function after the `connect` function (after line 192):

```typescript
const fetchPersistedLogs = async () => {
    try {
        const { data } = await computeApi.get(`/deployment/logs/${deploymentId}`)
        if (data.logs && data.logs.length > 0) {
            const formatted = data.logs.map((log: string) => cleanAnsi(log)).filter((l: string) => l.trim().length > 0)
            setLines(formatted)
            setLogSource("persisted")
        }
    } catch (err) {
        console.error("Failed to fetch persisted logs:", err)
    }
}
```

- [ ] **Step 2: Call fetchPersistedLogs on WS failure/disconnect**

In the `connect` function, modify the `ws.onclose` handler (lines 179-184) to attempt loading persisted logs:

```typescript
ws.onclose = () => {
    console.log("[TerminalLogs] WS Closed")
    if (status !== "error") {
        setStatus("disconnected")
    }
    // If we have no lines, try to load persisted logs
    if (lines.length === 0) {
        fetchPersistedLogs()
    }
}
```

Also in the `ws.onerror` handler (lines 173-177), and in the catch block (lines 186-191):

```typescript
ws.onerror = (e) => {
    console.error("[TerminalLogs] WS Error:", e)
    setStatus("error")
    setError("WebSocket connection failed.")
    fetchPersistedLogs()
}
```

```typescript
} catch (err: any) {
    console.error("Failed to setup log stream:", err)
    setStatus("error")
    setError(err.message || "Failed to initialize log stream.")
    toast.error("Log stream initialization failed")
    fetchPersistedLogs()
}
```

- [ ] **Step 3: Update the status indicator to show "Saved Logs" when source is persisted**

When `logSource === "persisted"`, set `status` to `"connected"` is wrong — we need a distinct indicator. Modify the `ws.onopen` handler to track live source:

```typescript
ws.onopen = () => {
    console.log("[TerminalLogs] Connected to sidecar WS")
    setStatus("connected")
    setLogSource("live")
    ws.send(JSON.stringify(subscription))
}
```

In the header section (around line 250-268), add a case for persisted logs. After the `status === "connecting"` block and before the final `else` (the WifiOff/Stopped indicator), add a check for persisted source:

```typescript
) : logSource === "persisted" ? (
    <div className="flex items-center gap-1.5 ml-2">
        <span className="relative flex h-2 w-2">
            <span className="relative inline-flex rounded-full h-2 w-2 bg-amber-500"></span>
        </span>
        <span className="text-[10px] font-mono text-amber-500/80 uppercase tracking-widest font-bold">Saved Logs</span>
    </div>
```

- [ ] **Step 4: Verify the dashboard builds**

Run: `cd /storage/intern/hooman/InferiaLLM/apps/dashboard && npm run build`
Expected: Build succeeds with no TypeScript errors

- [ ] **Step 5: Commit**

```bash
git add apps/dashboard/src/components/deployment/TerminalLogs.tsx
git commit -m "feat: load persisted logs in dashboard when live stream unavailable (#169)"
```

---

### Task 8: Run full test suite and verify

**Files:** None (verification only)

- [ ] **Step 1: Run all log persistence tests**

Run: `cd /storage/intern/hooman/InferiaLLM && python -m pytest package/src/inferia/services/orchestration/test/model_deployment/test_log_store.py -v`
Expected: All 16 tests pass

- [ ] **Step 2: Run all model_deployment tests**

Run: `cd /storage/intern/hooman/InferiaLLM && python -m pytest package/src/inferia/services/orchestration/test/model_deployment/ -v`
Expected: All tests pass (no regressions)

- [ ] **Step 3: Run dashboard build**

Run: `cd /storage/intern/hooman/InferiaLLM/apps/dashboard && npm run build`
Expected: Clean build

- [ ] **Step 4: Commit — no changes expected, just verification**

If all passes, no commit needed. If any fixes were required, commit them:
```bash
git commit -m "fix: address test/build issues in log persistence implementation (#169)"
```

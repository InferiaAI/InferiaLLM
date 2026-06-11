"""
Deployment terminal log persistence via Elasticsearch.

Provides DeploymentLogStore (ES client wrapper) and DeploymentLogBuffer
(circular buffer with periodic flush) for persisting terminal logs that
are sniffed from the WebSocket relay.
"""

import asyncio
import logging
from collections import deque
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
        pending = buf_list[self._flush_cursor:]
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

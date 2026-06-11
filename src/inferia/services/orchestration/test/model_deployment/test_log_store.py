"""Tests for deployment log persistence."""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from collections import deque
from datetime import datetime, timezone

from inferia.services.orchestration.config import Settings

try:
    import elasticsearch
    HAS_ELASTICSEARCH = True
except ImportError:
    HAS_ELASTICSEARCH = False

_skip_no_es = pytest.mark.skipif(
    not HAS_ELASTICSEARCH, reason="elasticsearch package not installed"
)


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


class TestDeploymentLogStore:
    @pytest.mark.asyncio
    async def test_init_sets_unavailable_when_no_url(self):
        from inferia.services.orchestration.services.model_deployment.log_store import (
            DeploymentLogStore,
        )

        store = DeploymentLogStore(elasticsearch_url=None)
        await store.initialize()
        assert store.available is False

    @_skip_no_es
    @pytest.mark.asyncio
    async def test_init_sets_available_when_es_responds(self):
        from inferia.services.orchestration.services.model_deployment.log_store import (
            DeploymentLogStore,
        )

        with patch(
            "elasticsearch.AsyncElasticsearch"
        ) as MockES:
            mock_client = AsyncMock()
            mock_client.ping = AsyncMock(return_value=True)
            MockES.return_value = mock_client

            store = DeploymentLogStore(elasticsearch_url="http://localhost:9200")
            await store.initialize()
            assert store.available is True

    @_skip_no_es
    @pytest.mark.asyncio
    async def test_init_sets_unavailable_when_es_unreachable(self):
        from inferia.services.orchestration.services.model_deployment.log_store import (
            DeploymentLogStore,
        )

        with patch(
            "elasticsearch.AsyncElasticsearch"
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

    @_skip_no_es
    @pytest.mark.asyncio
    async def test_flush_bulk_indexes_lines(self):
        from inferia.services.orchestration.services.model_deployment.log_store import (
            DeploymentLogStore,
        )

        with patch(
            "elasticsearch.AsyncElasticsearch"
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

    @_skip_no_es
    @pytest.mark.asyncio
    async def test_get_logs_queries_es_by_deployment_id(self):
        from inferia.services.orchestration.services.model_deployment.log_store import (
            DeploymentLogStore,
        )

        with patch(
            "elasticsearch.AsyncElasticsearch"
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

    @_skip_no_es
    @pytest.mark.asyncio
    async def test_get_max_line_number_returns_zero_when_no_docs(self):
        from inferia.services.orchestration.services.model_deployment.log_store import (
            DeploymentLogStore,
        )

        with patch(
            "elasticsearch.AsyncElasticsearch"
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

    @_skip_no_es
    @pytest.mark.asyncio
    async def test_flush_sends_pending_lines_to_store(self):
        from inferia.services.orchestration.services.model_deployment.log_store import (
            DeploymentLogBuffer,
            DeploymentLogStore,
        )

        with patch(
            "elasticsearch.AsyncElasticsearch"
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

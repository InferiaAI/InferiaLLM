"""Tests for error sanitization in data service endpoints (issue #47).

Internal error details must NOT be exposed to API consumers.
"""

import os
import pytest
from unittest.mock import patch, MagicMock
from httpx import AsyncClient, ASGITransport


INTERNAL_MSG = "sqlalchemy.exc.OperationalError: (psycopg2.OperationalError) connection refused"
TEST_API_KEY = "test-key-for-unit-tests"
HEADERS = {"X-Internal-API-Key": TEST_API_KEY}


@pytest.fixture(autouse=True)
def set_api_key():
    with patch.dict(os.environ, {"INTERNAL_API_KEY": TEST_API_KEY}):
        from importlib import reload
        import inferia.services.data.config as cfg
        reload(cfg)
        import inferia.services.data.app as app_mod
        reload(app_mod)
        yield app_mod


@pytest.fixture
def mock_data_engine(set_api_key):
    with patch.object(set_api_key, "data_engine") as mock:
        mock.retrieve_context = MagicMock(side_effect=Exception(INTERNAL_MSG))
        mock.add_documents = MagicMock(side_effect=Exception(INTERNAL_MSG))
        yield mock


@pytest.fixture
def mock_prompt_engine(set_api_key):
    with patch.object(set_api_key, "prompt_engine") as mock:
        mock.process_prompt = MagicMock(side_effect=Exception(INTERNAL_MSG))
        mock.process_prompt_from_content = MagicMock(side_effect=Exception(INTERNAL_MSG))
        mock.rewrite_prompt = MagicMock(side_effect=Exception(INTERNAL_MSG))
        yield mock


class TestDataServiceErrorSanitization:

    @pytest.mark.asyncio
    async def test_retrieve_error_not_exposed(self, set_api_key, mock_data_engine):
        transport = ASGITransport(app=set_api_key.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/retrieve",
                json={"collection_name": "test", "query": "q", "n_results": 1},
                headers=HEADERS,
            )
        assert resp.status_code == 500
        assert INTERNAL_MSG not in resp.json().get("detail", "")

    @pytest.mark.asyncio
    async def test_ingest_error_not_exposed(self, set_api_key, mock_data_engine):
        transport = ASGITransport(app=set_api_key.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/ingest",
                json={
                    "collection_name": "test",
                    "documents": ["doc"],
                    "metadatas": [{}],
                    "ids": ["id1"],
                },
                headers=HEADERS,
            )
        assert resp.status_code == 500
        assert INTERNAL_MSG not in resp.json().get("detail", "")

    @pytest.mark.asyncio
    async def test_process_error_not_exposed(self, set_api_key, mock_prompt_engine):
        transport = ASGITransport(app=set_api_key.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/process",
                json={
                    "messages": [{"role": "user", "content": "test"}],
                    "model": "gpt-4",
                    "template_id": "test",
                    "template_vars": {},
                },
                headers=HEADERS,
            )
        assert resp.status_code == 500
        assert INTERNAL_MSG not in resp.json().get("detail", "")

    @pytest.mark.asyncio
    async def test_rewrite_error_not_exposed(self, set_api_key, mock_prompt_engine):
        transport = ASGITransport(app=set_api_key.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/rewrite",
                json={"prompt": "test", "goal": "simplify"},
                headers=HEADERS,
            )
        assert resp.status_code == 500
        assert INTERNAL_MSG not in resp.json().get("detail", "")

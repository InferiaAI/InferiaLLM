"""Tests for error sanitization in data router endpoints (issue #47).

Internal error details in /internal/data/* endpoints must NOT be
exposed to API consumers.
"""

import pytest
from unittest.mock import patch, MagicMock
from fastapi import FastAPI
from httpx import AsyncClient, ASGITransport

from inferia.services.data.router import router


INTERNAL_MSG = "FileNotFoundError: /var/data/secrets/db_password.txt"


@pytest.fixture
def test_app():
    app = FastAPI()
    app.include_router(router)
    return app


@pytest.fixture
def mock_data_engine():
    with patch("inferia.services.data.router.data_engine") as mock:
        mock.retrieve_context = MagicMock(side_effect=Exception(INTERNAL_MSG))
        mock.add_documents = MagicMock(side_effect=Exception(INTERNAL_MSG))
        yield mock


class TestDataRouterErrorSanitization:

    @pytest.mark.asyncio
    async def test_router_ingest_error_not_exposed(self, test_app, mock_data_engine):
        transport = ASGITransport(app=test_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/internal/data/ingest",
                json={
                    "collection_name": "test",
                    "documents": ["doc"],
                    "metadatas": [{}],
                    "ids": ["id1"],
                },
            )
        assert resp.status_code == 500
        assert INTERNAL_MSG not in resp.json().get("detail", "")

    @pytest.mark.asyncio
    async def test_router_retrieve_error_not_exposed(self, test_app, mock_data_engine):
        transport = ASGITransport(app=test_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/internal/data/retrieve",
                json={"collection_name": "test", "query": "q", "n_results": 1},
            )
        assert resp.status_code == 500
        assert INTERNAL_MSG not in resp.json().get("detail", "")

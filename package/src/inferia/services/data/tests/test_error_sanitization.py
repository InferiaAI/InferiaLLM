"""Tests for error sanitization in data service endpoints (issue #47).

Internal error details must NOT be exposed to API consumers.
"""

import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from fastapi import HTTPException

from inferia.services.data.app import retrieve, ingest, process, rewrite


INTERNAL_MSG = "sqlalchemy.exc.OperationalError: (psycopg2.OperationalError) connection refused"


class TestDataServiceErrorSanitization:

    @pytest.mark.asyncio
    async def test_retrieve_error_not_exposed(self):
        request = MagicMock()
        request.collection_name = "test"
        request.query = "q"
        request.org_id = "default"
        request.n_results = 1

        with patch("inferia.services.data.app.data_engine") as mock:
            mock.retrieve_context = MagicMock(side_effect=Exception(INTERNAL_MSG))
            with pytest.raises(HTTPException) as exc_info:
                await retrieve(request)
            assert exc_info.value.status_code == 500
            assert INTERNAL_MSG not in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_ingest_error_not_exposed(self):
        request = MagicMock()
        request.collection_name = "test"
        request.documents = ["doc"]
        request.metadatas = [{}]
        request.ids = ["id1"]
        request.org_id = "default"

        with patch("inferia.services.data.app.data_engine") as mock:
            mock.add_documents = MagicMock(side_effect=Exception(INTERNAL_MSG))
            with pytest.raises(HTTPException) as exc_info:
                await ingest(request)
            assert exc_info.value.status_code == 500
            assert INTERNAL_MSG not in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_process_error_not_exposed(self):
        request = MagicMock()
        request.template_content = None
        request.template_id = "test"
        request.template_vars = {}

        with patch("inferia.services.data.app.prompt_engine") as mock:
            mock.process_prompt = MagicMock(side_effect=Exception(INTERNAL_MSG))
            with pytest.raises(HTTPException) as exc_info:
                await process(request)
            assert exc_info.value.status_code == 500
            assert INTERNAL_MSG not in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_rewrite_error_not_exposed(self):
        request = MagicMock()
        request.prompt = "test"
        request.goal = "simplify"

        with patch("inferia.services.data.app.prompt_engine") as mock:
            mock.rewrite_prompt = AsyncMock(side_effect=Exception(INTERNAL_MSG))
            with pytest.raises(HTTPException) as exc_info:
                await rewrite(request)
            assert exc_info.value.status_code == 500
            assert INTERNAL_MSG not in exc_info.value.detail

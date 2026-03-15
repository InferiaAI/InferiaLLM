"""Tests for error sanitization in data router endpoints (issue #47).

Internal error details in /internal/data/* endpoints must NOT be
exposed to API consumers.
"""

import pytest
from unittest.mock import patch, MagicMock
from fastapi import HTTPException

from inferia.services.data.router import ingest_documents, retrieve_context


INTERNAL_MSG = "FileNotFoundError: /var/data/secrets/db_password.txt"


class TestDataRouterErrorSanitization:

    @pytest.mark.asyncio
    async def test_router_ingest_error_not_exposed(self):
        request = MagicMock()
        request.collection_name = "test"
        request.documents = ["doc"]
        request.metadatas = [{}]
        request.ids = ["id1"]

        with patch("inferia.services.data.router.data_engine") as mock:
            mock.add_documents = MagicMock(side_effect=Exception(INTERNAL_MSG))
            with pytest.raises(HTTPException) as exc_info:
                await ingest_documents(request)
            assert exc_info.value.status_code == 500
            assert INTERNAL_MSG not in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_router_retrieve_error_not_exposed(self):
        request = MagicMock()
        request.collection_name = "test"
        request.query = "q"
        request.n_results = 1

        with patch("inferia.services.data.router.data_engine") as mock:
            mock.retrieve_context = MagicMock(side_effect=Exception(INTERNAL_MSG))
            with pytest.raises(HTTPException) as exc_info:
                await retrieve_context(request)
            assert exc_info.value.status_code == 500
            assert INTERNAL_MSG not in exc_info.value.detail

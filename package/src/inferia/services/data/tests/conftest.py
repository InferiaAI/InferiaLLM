"""Shared test fixtures for data service tests."""

import pytest
from unittest.mock import AsyncMock, MagicMock
from io import BytesIO


@pytest.fixture
def mock_chroma_client():
    """Mock ChromaDB client with in-memory collection store."""
    client = MagicMock()
    collection = MagicMock()
    collection.query.return_value = {"documents": [["doc1", "doc2"]], "distances": [[0.1, 0.2]]}
    collection.add.return_value = None
    client.get_or_create_collection.return_value = collection
    return client


@pytest.fixture
def mock_file_upload():
    """Create mock UploadFile with controlled content."""
    def _make(filename="test.txt", content=b"Hello World", content_type="text/plain", size=None):
        upload = MagicMock()
        upload.filename = filename
        upload.content_type = content_type
        upload.size = size or len(content)
        upload.read = AsyncMock(return_value=content)
        upload.seek = AsyncMock()
        upload.file = BytesIO(content)
        return upload
    return _make

"""Shared test fixtures for common module tests."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.fixture
def mock_http_client():
    """Mock InternalHttpClient returning controlled responses."""
    client = AsyncMock()
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {}
    response.text = ""
    client.get.return_value = response
    client.post.return_value = response
    client.put.return_value = response
    client.delete.return_value = response
    return client

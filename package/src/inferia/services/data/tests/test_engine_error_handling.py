"""Tests for data engine error handling."""

import pytest
from unittest.mock import MagicMock

from inferia.services.data.engine import DataEngine


class TestDataEngineErrors:
    """Verify data engine error paths."""

    def test_retrieve_context_no_client_returns_empty(self):
        """ChromaDB client not initialized returns empty results."""
        engine = DataEngine()
        engine.client = None
        engine.initialize_client = MagicMock()  # No-op, keeps client as None

        result = engine.retrieve_context("test_collection", "query", "org1")
        assert result == []

    def test_list_collections_no_client_returns_empty(self):
        """No ChromaDB client returns empty collection list."""
        engine = DataEngine()
        engine.client = None
        engine.initialize_client = MagicMock()

        result = engine.list_collections("org1")
        assert result == []

    def test_add_documents_no_collection_returns_false(self):
        """Failed collection access returns False."""
        engine = DataEngine()
        engine.client = None
        engine.initialize_client = MagicMock()

        result = engine.add_documents(
            collection_name="test",
            documents=["doc"],
            metadatas=[{}],
            ids=["id1"],
        )
        assert result is False

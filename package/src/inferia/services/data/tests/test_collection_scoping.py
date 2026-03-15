"""Tests for ChromaDB collection scoping by org_id.

Ensures _get_scoped_name enforces tenant isolation by requiring a valid org_id
and never falling back to an unscoped collection name.
"""

import pytest
from unittest.mock import MagicMock

from inferia.services.data.engine import DataEngine


@pytest.fixture
def engine():
    """Create a DataEngine instance with a mocked ChromaDB client."""
    eng = DataEngine()
    eng.client = MagicMock()
    return eng


class TestGetScopedName:
    """Tests for DataEngine._get_scoped_name tenant isolation."""

    def test_raises_valueerror_when_org_id_is_none(self, engine):
        """org_id=None must raise ValueError, not fall back to unscoped name."""
        with pytest.raises(ValueError, match="org_id is required"):
            engine._get_scoped_name("my_collection", org_id=None)

    def test_raises_valueerror_when_org_id_is_empty_string(self, engine):
        """org_id='' must raise ValueError, not fall back to unscoped name."""
        with pytest.raises(ValueError, match="org_id is required"):
            engine._get_scoped_name("my_collection", org_id="")

    def test_valid_org_id_returns_scoped_name(self, engine):
        """A valid org_id must produce a scoped collection name."""
        result = engine._get_scoped_name("my_collection", org_id="abc123")
        assert result == "org_abc123_my_collection"

    def test_scoped_name_format(self, engine):
        """Verify the exact format: org_{org_id}_{collection_name}."""
        org_id = "tenant-42"
        collection = "documents"
        result = engine._get_scoped_name(collection, org_id=org_id)
        assert result == f"org_{org_id}_{collection}"

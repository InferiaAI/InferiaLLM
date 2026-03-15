"""Tests for filename sanitization in upload metadata.

Ensures path traversal sequences and other unsafe filename components
are stripped before being stored in ChromaDB metadata.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


def make_upload(filename="test.txt", content=b"Hello World", content_type="text/plain"):
    upload = MagicMock()
    upload.filename = filename
    upload.content_type = content_type
    upload.read = AsyncMock(return_value=content)
    return upload


class TestAppUploadFilenameSanitization:
    """Verify that data/app.py upload_document sanitizes filenames in metadata."""

    @pytest.mark.asyncio
    async def test_path_traversal_unix_sanitized(self):
        """../../etc/passwd should become just 'passwd' in metadata."""
        with patch("inferia.services.data.app.data_engine") as mock_engine, \
             patch("inferia.services.data.app.parser") as mock_parser:
            from inferia.services.data.app import upload_document

            mock_parser.extract_text_from_bytes.return_value = "file content"
            mock_engine.add_documents.return_value = True

            upload = make_upload(filename="../../etc/passwd.txt")
            result = await upload_document(
                file=upload, collection_name="test", org_id=None
            )

            call_args = mock_engine.add_documents.call_args
            metadata = call_args.kwargs["metadatas"][0]
            assert metadata["source"] == "passwd.txt"
            assert "/" not in metadata["source"]
            assert ".." not in metadata["source"]

    @pytest.mark.asyncio
    async def test_path_traversal_windows_sanitized(self):
        r"""C:\Windows\system32\config.txt should become just 'config.txt'."""
        with patch("inferia.services.data.app.data_engine") as mock_engine, \
             patch("inferia.services.data.app.parser") as mock_parser:
            from inferia.services.data.app import upload_document

            mock_parser.extract_text_from_bytes.return_value = "file content"
            mock_engine.add_documents.return_value = True

            upload = make_upload(filename=r"C:\Windows\system32\config.txt")
            result = await upload_document(
                file=upload, collection_name="test", org_id=None
            )

            call_args = mock_engine.add_documents.call_args
            metadata = call_args.kwargs["metadatas"][0]
            assert metadata["source"] == "config.txt"

    @pytest.mark.asyncio
    async def test_normal_filename_unchanged(self):
        """A normal filename like document.pdf should stay as-is."""
        with patch("inferia.services.data.app.data_engine") as mock_engine, \
             patch("inferia.services.data.app.parser") as mock_parser:
            from inferia.services.data.app import upload_document

            mock_parser.extract_text_from_bytes.return_value = "file content"
            mock_engine.add_documents.return_value = True

            upload = make_upload(filename="document.pdf", content_type="application/pdf")
            result = await upload_document(
                file=upload, collection_name="test", org_id=None
            )

            call_args = mock_engine.add_documents.call_args
            metadata = call_args.kwargs["metadatas"][0]
            assert metadata["source"] == "document.pdf"

    @pytest.mark.asyncio
    async def test_empty_filename_becomes_unknown(self):
        """Empty string filename should become 'unknown'."""
        with patch("inferia.services.data.app.data_engine") as mock_engine, \
             patch("inferia.services.data.app.parser") as mock_parser:
            from inferia.services.data.app import upload_document
            from fastapi import HTTPException

            upload = make_upload(filename="")
            with pytest.raises(HTTPException) as exc:
                await upload_document(
                    file=upload, collection_name="test", org_id=None
                )
            assert exc.value.status_code == 400


class TestRouterUploadFilenameSanitization:
    """Verify that data/router.py upload_document sanitizes filenames in metadata."""

    @pytest.mark.asyncio
    async def test_path_traversal_unix_sanitized(self):
        """../../etc/passwd.txt should become just 'passwd.txt' in metadata."""
        with patch("inferia.services.data.router.data_engine") as mock_engine, \
             patch("inferia.services.data.router.parser") as mock_parser:
            from inferia.services.data.router import upload_document

            mock_parser.extract_text = AsyncMock(return_value="file content")
            mock_engine.add_documents.return_value = True

            upload = make_upload(filename="../../etc/passwd.txt")
            result = await upload_document(file=upload, collection_name="test")

            call_args = mock_engine.add_documents.call_args
            metadata = call_args.kwargs["metadatas"][0]
            assert metadata["source"] == "passwd.txt"
            assert "/" not in metadata["source"]
            assert ".." not in metadata["source"]

    @pytest.mark.asyncio
    async def test_path_traversal_windows_sanitized(self):
        r"""C:\Windows\system32\config.txt should become just 'config.txt'."""
        with patch("inferia.services.data.router.data_engine") as mock_engine, \
             patch("inferia.services.data.router.parser") as mock_parser:
            from inferia.services.data.router import upload_document

            mock_parser.extract_text = AsyncMock(return_value="file content")
            mock_engine.add_documents.return_value = True

            upload = make_upload(filename=r"C:\Windows\system32\config.txt")
            result = await upload_document(file=upload, collection_name="test")

            call_args = mock_engine.add_documents.call_args
            metadata = call_args.kwargs["metadatas"][0]
            assert metadata["source"] == "config.txt"

    @pytest.mark.asyncio
    async def test_normal_filename_unchanged(self):
        """A normal filename like document.txt should stay as-is."""
        with patch("inferia.services.data.router.data_engine") as mock_engine, \
             patch("inferia.services.data.router.parser") as mock_parser:
            from inferia.services.data.router import upload_document

            mock_parser.extract_text = AsyncMock(return_value="file content")
            mock_engine.add_documents.return_value = True

            upload = make_upload(filename="document.txt")
            result = await upload_document(file=upload, collection_name="test")

            call_args = mock_engine.add_documents.call_args
            metadata = call_args.kwargs["metadatas"][0]
            assert metadata["source"] == "document.txt"

    @pytest.mark.asyncio
    async def test_none_filename_becomes_unknown(self):
        """None filename should become 'unknown' in metadata."""
        with patch("inferia.services.data.router.data_engine") as mock_engine, \
             patch("inferia.services.data.router.parser") as mock_parser:
            from inferia.services.data.router import upload_document

            mock_parser.extract_text = AsyncMock(return_value="file content")
            mock_engine.add_documents.return_value = True

            upload = make_upload(filename=None)
            result = await upload_document(file=upload, collection_name="test")

            call_args = mock_engine.add_documents.call_args
            metadata = call_args.kwargs["metadatas"][0]
            assert metadata["source"] == "unknown"

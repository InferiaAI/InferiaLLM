"""Tests for data upload security."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import HTTPException


def make_upload(filename="test.txt", content=b"Hello World", content_type="text/plain"):
    upload = MagicMock()
    upload.filename = filename
    upload.content_type = content_type
    upload.read = AsyncMock(return_value=content)
    return upload


class TestUploadSecurity:
    """Verify file upload validation."""

    @pytest.mark.asyncio
    async def test_disallowed_extension_rejected(self):
        from inferia.services.data.app import upload_document

        upload = make_upload(filename="malware.exe")
        with pytest.raises(HTTPException) as exc:
            await upload_document(file=upload, collection_name="test")
        assert exc.value.status_code == 400
        assert "not allowed" in str(exc.value.detail)

    @pytest.mark.asyncio
    async def test_oversized_file_rejected(self):
        from inferia.services.data.app import upload_document

        big_content = b"x" * (51 * 1024 * 1024)
        upload = make_upload(content=big_content)
        with pytest.raises(HTTPException) as exc:
            await upload_document(file=upload, collection_name="test")
        assert exc.value.status_code == 413

    @pytest.mark.asyncio
    async def test_empty_file_rejected(self):
        from inferia.services.data.app import upload_document

        upload = make_upload(content=b"")
        with pytest.raises(HTTPException) as exc:
            await upload_document(file=upload, collection_name="test")
        assert exc.value.status_code == 400
        assert "empty" in str(exc.value.detail).lower()

    @pytest.mark.asyncio
    async def test_no_filename_rejected(self):
        from inferia.services.data.app import upload_document

        upload = make_upload()
        upload.filename = None
        with pytest.raises(HTTPException) as exc:
            await upload_document(file=upload, collection_name="test")
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_path_traversal_extension_not_allowed(self):
        """Path traversal filenames without valid extension are rejected."""
        from inferia.services.data.app import upload_document

        upload = make_upload(filename="../../etc/passwd")
        with pytest.raises(HTTPException) as exc:
            await upload_document(file=upload, collection_name="test")
        assert exc.value.status_code == 400
        assert "not allowed" in str(exc.value.detail)

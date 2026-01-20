"""
File Parser Module.
Handles text extraction from various file formats.
"""

from fastapi import UploadFile, HTTPException, status
from io import BytesIO
import logging
from typing import Optional

# Import parsing libraries
try:
    import pypdf
except ImportError:
    pypdf = None

try:
    from docx import Document
except ImportError:
    Document = None

logger = logging.getLogger(__name__)

class FileParser:
    """Parses uploaded files to extract text."""
    
    @staticmethod
    def _validate_file_size(file: UploadFile, max_size_mb: int = 10):
        """Check if file size is within limits (approximate)."""
        # Note: accurate size check requires reading, but we can check content-length header if available
        # logic skipped for simplicity, reliant on server limits
        pass

    @staticmethod
    async def parse_pdf(file: UploadFile) -> str:
        """Extract text from PDF."""
        if not pypdf:
            raise HTTPException(
                status_code=status.HTTP_501_NOT_IMPLEMENTED,
                detail="PDF parsing not available (pypdf not installed)"
            )
        
        try:
            content = await file.read()
            pdf_reader = pypdf.PdfReader(BytesIO(content))
            text = ""
            for page in pdf_reader.pages:
                text += page.extract_text() + "\n"
            return text
        except Exception as e:
            logger.error(f"Error parsing PDF {file.filename}: {e}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Failed to parse PDF: {str(e)}"
            )

    @staticmethod
    async def parse_docx(file: UploadFile) -> str:
        """Extract text from DOCX."""
        if not Document:
            raise HTTPException(
                status_code=status.HTTP_501_NOT_IMPLEMENTED,
                detail="DOCX parsing not available (python-docx not installed)"
            )
            
        try:
            content = await file.read()
            doc = Document(BytesIO(content))
            return "\n".join([para.text for para in doc.paragraphs])
        except Exception as e:
            logger.error(f"Error parsing DOCX {file.filename}: {e}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Failed to parse DOCX: {str(e)}"
            )

    @staticmethod
    async def parse_text(file: UploadFile) -> str:
        """Extract text from plain text file."""
        try:
            content = await file.read()
            return content.decode("utf-8")
        except UnicodeDecodeError:
            try:
                return content.decode("latin-1")
            except Exception:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Could not decode text file (not UTF-8 or Latin-1)"
                )
        except Exception as e:
            logger.error(f"Error parsing text file {file.filename}: {e}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Failed to parse text file: {str(e)}"
            )

    @classmethod
    async def extract_text(cls, file: UploadFile) -> str:
        """Determines file type and extracts text."""
        content_type = file.content_type
        filename = file.filename.lower() if file.filename else ""
        
        logger.info(f"Extracting text from {filename} ({content_type})")
        
        # Reset file cursor just in case
        await file.seek(0)
        
        if content_type == "application/pdf" or filename.endswith(".pdf"):
            return await cls.parse_pdf(file)
        elif content_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document" or filename.endswith(".docx"):
            return await cls.parse_docx(file)
        elif content_type == "text/plain" or filename.endswith(".txt"):
            return await cls.parse_text(file)
        else:
             raise HTTPException(
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                detail=f"Unsupported file type: {content_type}. Supported: PDF, DOCX, TXT"
            )

parser = FileParser()

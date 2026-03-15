from fastapi import APIRouter, HTTPException, status, UploadFile, File, Form
from .api_models import IngestRequest, RetrieveRequest, RetrieveResponse
from .engine import data_engine
from .parser import parser
import asyncio
import uuid
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/internal/data", tags=["Data Engine"])


@router.post("/upload", status_code=status.HTTP_201_CREATED)
async def upload_document(
    file: UploadFile = File(...), collection_name: str = Form(...)
):
    """
    Upload and ingest a file (PDF, DOCX, TXT).
    """
    try:
        # 1. Parse file content
        text_content = await parser.extract_text(file)

        if not text_content.strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="File content is empty or could not be extracted",
            )

        # 2. Ingest into Data Engine
        # We treat the whole file as one "document" for now
        # Ideally, we would chunk it.
        doc_id = str(uuid.uuid4())
        metadata = {"source": file.filename, "type": "file_upload"}

        success = await asyncio.to_thread(
            data_engine.add_documents,
            collection_name=collection_name,
            documents=[text_content],
            metadatas=[metadata],
            ids=[doc_id],
        )

        if not success:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to ingest parsed document (Data Engine error)",
            )

        return {
            "status": "success",
            "filename": file.filename,
            "char_count": len(text_content),
            "doc_id": doc_id,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error uploading file: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error",
        )


@router.post("/ingest", status_code=status.HTTP_201_CREATED)
async def ingest_documents(request: IngestRequest):
    """
    Ingest documents into the vector database.
    """
    # Auto-generate IDs if not provided
    if not request.ids:
        request.ids = [str(uuid.uuid4()) for _ in request.documents]

    # Auto-generate empty metadata if not provided
    # Auto-generate default metadata if not provided or empty
    if not request.metadatas:
        request.metadatas = [
            {"source": "ingest_api", "index": i}
            for i, _ in enumerate(request.documents)
        ]
    else:
        # Validate existing metadatas are not empty dicts
        for i, meta in enumerate(request.metadatas):
            if not meta:
                request.metadatas[i] = {"source": "ingest_api_fallback", "index": i}

    try:
        success = await asyncio.to_thread(
            data_engine.add_documents,
            collection_name=request.collection_name,
            documents=request.documents,
            metadatas=request.metadatas,
            ids=request.ids,
        )

        if not success:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to ingest documents (Data Engine error)",
            )

        return {"status": "success", "count": len(request.documents)}

    except Exception as e:
        logger.exception("Error ingesting documents: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error",
        )


@router.post("/retrieve", response_model=RetrieveResponse)
async def retrieve_context(request: RetrieveRequest):
    """
    Retrieve context for a query from the vector database.
    """
    try:
        context = await asyncio.to_thread(
            data_engine.retrieve_context,
            collection_name=request.collection_name,
            query=request.query,
            n_results=request.n_results,
        )
        return RetrieveResponse(context=context)

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error",
        )

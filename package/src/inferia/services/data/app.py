from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
import logging
import sys
from pathlib import Path
import os

# Add current directory to path
sys.path.append(str(Path(__file__).parent))

from inferia.services.data.engine import data_engine
from inferia.services.data.prompt_engine import prompt_engine

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("data-service")

# File upload security configuration
ALLOWED_EXTENSIONS = {".txt", ".pdf", ".docx", ".md", ".json", ".csv"}
MAX_FILE_SIZE_MB = 50  # Maximum file size in MB
MAX_FILE_SIZE_BYTES = MAX_FILE_SIZE_MB * 1024 * 1024

from contextlib import asynccontextmanager


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Start polling config from Gateway
    from inferia.services.data.config_manager import config_manager
    from inferia.services.data.service_config import settings

    config_manager.start_polling(
        gateway_url=settings.filtration_url, api_key=settings.internal_api_key
    )

    yield

    config_manager.stop_polling()


app = FastAPI(title="Data Service", version="0.1.0", lifespan=lifespan)

# CORS configuration
# Allow requests from dashboard and other known origins
allowed_origins = os.getenv(
    "ALLOWED_ORIGINS",
    "http://localhost:3000,http://localhost:5173,http://localhost:8001",
)
_allow_origins = [
    origin.strip() for origin in allowed_origins.split(",") if origin.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allow_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Requested-With"],
)


class RetrieveRequest(BaseModel):
    collection_name: str
    query: str
    org_id: Optional[str] = None
    n_results: int = 3


class IngestRequest(BaseModel):
    collection_name: str
    documents: List[str]
    metadatas: List[Dict[str, Any]]
    ids: List[str]
    org_id: Optional[str] = None


class ProcessRequest(BaseModel):
    template_id: Optional[str] = None
    template_content: Optional[str] = None
    variables: Optional[Dict[str, Any]] = None


class TokenCheckRequest(BaseModel):
    text: str
    budget: int
    model_name: str = "gpt-3.5-turbo"


class RewriteRequest(BaseModel):
    prompt: str
    goal: str = "clarity"


class AssembleContextRequest(BaseModel):
    query: str
    collection_name: str
    org_id: str
    n_results: int = 3


from inferia.services.data.parser import parser
from fastapi import UploadFile, File, Form, status
import uuid

# ... existing imports ...


@app.post("/upload")
async def upload_document(
    file: UploadFile = File(...),
    collection_name: str = Form(...),
    org_id: Optional[str] = Form(None),
):
    """
    Upload and ingest a file (PDF, DOCX, TXT, MD, JSON, CSV).
    Validates file type, size, and content before processing.
    """
    try:
        # 1. Validate filename
        if not file.filename:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Filename is required",
            )

        # 2. Validate file extension
        file_ext = Path(file.filename).suffix.lower()
        if file_ext not in ALLOWED_EXTENSIONS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"File type '{file_ext}' not allowed. Allowed types: {', '.join(ALLOWED_EXTENSIONS)}",
            )

        # 3. Validate content type (basic check)
        allowed_content_types = {
            "text/plain",
            "application/pdf",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "text/markdown",
            "application/json",
            "text/csv",
            "application/octet-stream",
        }
        if file.content_type and file.content_type not in allowed_content_types:
            logger.warning(
                f"Suspicious content type: {file.content_type} for file {file.filename}"
            )

        # 4. Read and validate file size
        file_content = await file.read()
        if len(file_content) > MAX_FILE_SIZE_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"File size exceeds maximum limit of {MAX_FILE_SIZE_MB}MB",
            )

        if len(file_content) == 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="File is empty",
            )

        # 5. Reset file position for parser
        await file.seek(0)

        # 6. Parse file content
        text_content = await parser.extract_text(file)

        if not text_content.strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="File content is empty or could not be extracted",
            )

        # 2. Ingest into Data Engine
        doc_id = str(uuid.uuid4())
        metadata = {"source": file.filename, "type": "file_upload"}

        success = data_engine.add_documents(
            collection_name=collection_name,
            documents=[text_content],
            metadatas=[metadata],
            ids=[doc_id],
            org_id=str(org_id) if org_id else "default",
        )

        if not success:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to ingest parsed document",
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
            detail=f"Error uploading file: {str(e)}",
        )


@app.post("/retrieve")
async def retrieve(request: RetrieveRequest):
    """
    Retrieve context from the Vector Database.
    """
    try:
        results = data_engine.retrieve_context(
            collection_name=request.collection_name,
            query=request.query,
            org_id=str(request.org_id) if request.org_id else "default",
            n_results=request.n_results,
        )
        return {"documents": results}
    except Exception as e:
        logger.error(f"Retrieve failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/ingest")
async def ingest(request: IngestRequest):
    """
    Ingest documents into the Vector Database.
    """
    try:
        success = data_engine.add_documents(
            collection_name=request.collection_name,
            documents=request.documents,
            metadatas=request.metadatas,
            ids=request.ids,
            org_id=str(request.org_id) if request.org_id else "default",
        )
        if not success:
            raise HTTPException(status_code=500, detail="Ingestion failed")
        return {"status": "ok"}
    except Exception as e:
        logger.error(f"Ingest failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# --- Prompt Endpoints ---


@app.post("/process")
async def process(request: ProcessRequest):
    """
    Process a prompt template.
    """
    try:
        if request.template_content:
            result = prompt_engine.process_prompt_from_content(
                content=request.template_content, variables=request.variables
            )
        else:
            result = prompt_engine.process_prompt(
                template_id=request.template_id, variables=request.variables
            )
        return {"content": result}
    except Exception as e:
        logger.error(f"Process failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/tokens/check")
async def check_tokens(request: TokenCheckRequest):
    """
    Check if text fits within token budget.
    """
    is_safe = prompt_engine.check_token_budget(
        text=request.text, budget=request.budget, model_name=request.model_name
    )
    count = prompt_engine.count_tokens(request.text, request.model_name)
    return {"is_safe": is_safe, "count": count}


@app.post("/rewrite")
async def rewrite(request: RewriteRequest):
    """
    Rewrite a prompt using LLM.
    """
    try:
        result = await prompt_engine.rewrite_prompt(
            prompt=request.prompt, goal=request.goal
        )
        return {"rewritten_prompt": result}
    except Exception as e:
        logger.error(f"Rewrite failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/health")
async def health():
    return {"status": "healthy", "service": "data"}


@app.get("/collections")
async def list_collections(org_id: str = "default"):
    """
    List all available collections for an organization.
    """
    try:
        collections = await data_engine.list_collections(org_id)
        return {"collections": collections}
    except Exception as e:
        logger.error(f"List collections failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/collections/{collection_name}/files")
async def list_collection_files(collection_name: str, org_id: str = "default"):
    """
    List files in a collection.
    """
    try:
        files = await data_engine.list_files(collection_name, org_id)
        return {"files": files}
    except Exception as e:
        logger.error(f"List collection files failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/context/assemble")
async def assemble_context(request: AssembleContextRequest):
    """
    Retrieve and format RAG context.
    """
    try:
        result = await prompt_engine.assemble_context(
            query=request.query,
            collection_name=request.collection_name,
            org_id=request.org_id,
            n_results=request.n_results,
        )
        return {"context": result}
    except Exception as e:
        logger.error(f"Context assembly failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8003)

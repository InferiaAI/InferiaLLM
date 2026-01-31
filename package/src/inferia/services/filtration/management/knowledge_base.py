from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File, Form
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List
import uuid

from db.database import get_db
from data.engine import data_engine
from data.parser import parser
from schemas.knowledge_base import KBFileResponse
from schemas.auth import PermissionEnum
from management.dependencies import get_current_user_context
from rbac.authorization import authz_service

router = APIRouter(tags=["Knowledge Base"])

@router.get("/data/collections", response_model=List[str])
async def list_knowledge_collections(
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    user_ctx = get_current_user_context(request)
    authz_service.require_permission(user_ctx, PermissionEnum.KB_LIST)
    return await data_engine.list_collections(user_ctx.org_id)

@router.post("/data/upload", status_code=201)
async def upload_knowledge_document(
    request: Request,
    file: UploadFile = File(...),
    collection_name: str = Form(...),
    db: AsyncSession = Depends(get_db)
):
    user_ctx = get_current_user_context(request)
    authz_service.require_permission(user_ctx, PermissionEnum.KB_ADD_DATA)
    
    try:
        text_content = await parser.extract_text(file)
        
        if not text_content.strip():
             raise HTTPException(
                status_code=400,
                detail="File content is empty or could not be extracted"
            )
            
        doc_id = str(uuid.uuid4())
        metadata = {
            "source": file.filename, 
            "type": "file_upload",
            "uploaded_by": user_ctx.user_id 
        }
        
        success = data_engine.add_documents(
            collection_name=collection_name,
            documents=[text_content],
            metadatas=[metadata],
            ids=[doc_id],
            org_id=user_ctx.org_id
        )
        
        if not success:
             raise HTTPException(status_code=500, detail="Failed to ingest document")

        # Log to audit service
        from audit.service import audit_service
        from audit.api_models import AuditLogCreate

        await audit_service.log_event(
            db,
            AuditLogCreate(
                user_id=user_ctx.user_id,
                action="knowledge_base.add_document",
                resource_type="knowledge_base_document",
                resource_id=doc_id,
                details={
                    "filename": file.filename,
                    "collection": collection_name
                },
                status="success"
            )
        )
            
        return {
            "status": "success", 
            "filename": file.filename,
            "doc_id": doc_id
        }
    except HTTPException:
        raise
    except Exception as e:
        print(f"DEBUG: Error uploading file: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error uploading file: {str(e)}")

@router.get("/data/collections/{collection_name}/files", response_model=List[KBFileResponse])
async def list_collection_files(
    collection_name: str,
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    user_ctx = get_current_user_context(request)
    authz_service.require_permission(user_ctx, PermissionEnum.KB_LIST)
    
    files = await data_engine.list_files(collection_name, user_ctx.org_id)
    return [
        KBFileResponse(
            filename=f["filename"],
            doc_id=f["doc_id"],
            uploaded_by=f["uploaded_by"],
            doc_count=f["doc_count"]
        ) for f in files
    ]

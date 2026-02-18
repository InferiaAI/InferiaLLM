from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File, Form
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List
import uuid
import logging

logger = logging.getLogger(__name__)

from inferia.services.api_gateway.db.database import get_db

import httpx
from inferia.services.api_gateway.config import settings
from inferia.services.api_gateway.schemas.knowledge_base import KBFileResponse
from inferia.services.api_gateway.schemas.auth import PermissionEnum
from inferia.services.api_gateway.management.dependencies import get_current_user_context
from inferia.services.api_gateway.rbac.authorization import authz_service

router = APIRouter(tags=["Knowledge Base"])


@router.get("/data/collections", response_model=List[str])
async def list_knowledge_collections(
    request: Request, db: AsyncSession = Depends(get_db)
):
    user_ctx = get_current_user_context(request)
    authz_service.require_permission(user_ctx, PermissionEnum.KB_LIST)

    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{settings.data_service_url}/collections",
            params={"org_id": user_ctx.org_id},
            timeout=5.0,
        )
        if response.status_code == 200:
            return response.json().get("collections", [])
        return []


@router.post("/data/upload", status_code=201)
async def upload_knowledge_document(
    request: Request,
    file: UploadFile = File(...),
    collection_name: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    user_ctx = get_current_user_context(request)
    authz_service.require_permission(user_ctx, PermissionEnum.KB_ADD_DATA)

    try:
        async with httpx.AsyncClient() as client:
            # Re-read file content for forwarding
            file_content = await file.read()
            files = {"file": (file.filename, file_content, file.content_type)}
            data = {"collection_name": collection_name, "org_id": user_ctx.org_id}

            response = await client.post(
                f"{settings.data_service_url}/upload",
                data=data,
                files=files,
                timeout=30.0,
            )

            if response.status_code != 200:
                raise HTTPException(
                    status_code=response.status_code,
                    detail=f"Data Service failed: {response.text}",
                )

            result = response.json()
            doc_id = result.get("doc_id")

        # Log to audit service
        from inferia.services.api_gateway.audit.service import audit_service
        from inferia.services.api_gateway.models import AuditLogCreate

        await audit_service.log_event(
            db,
            AuditLogCreate(
                user_id=user_ctx.user_id,
                action="knowledge_base.add_document",
                resource_type="knowledge_base_document",
                resource_id=doc_id,
                details={"filename": file.filename, "collection": collection_name},
                status="success",
            ),
        )

        return {"status": "success", "filename": file.filename, "doc_id": doc_id}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error uploading file: %s", e)
        raise HTTPException(status_code=500, detail=f"Error uploading file: {str(e)}")


@router.get(
    "/data/collections/{collection_name}/files", response_model=List[KBFileResponse]
)
async def list_collection_files(
    collection_name: str, request: Request, db: AsyncSession = Depends(get_db)
):
    user_ctx = get_current_user_context(request)
    authz_service.require_permission(user_ctx, PermissionEnum.KB_LIST)

    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{settings.data_service_url}/collections/{collection_name}/files",
            params={"org_id": user_ctx.org_id},
            timeout=10.0,
        )
        if response.status_code == 200:
            files = response.json().get("files", [])
            return [
                KBFileResponse(
                    filename=f["filename"],
                    doc_id=f["doc_id"],
                    uploaded_by=f["uploaded_by"],
                    doc_count=f["doc_count"],
                )
                for f in files
            ]
        return []

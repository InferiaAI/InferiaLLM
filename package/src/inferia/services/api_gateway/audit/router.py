from fastapi import APIRouter, Depends, Query, HTTPException, status, Security
from fastapi.security import APIKeyHeader
import os
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List, Optional
from datetime import datetime

from inferia.services.api_gateway.db.database import get_db
from inferia.services.api_gateway.audit.service import audit_service
from inferia.services.api_gateway.models import (
    AuditLogResponse,
    AuditLogFilter,
    AuditLogCreate,
    PermissionEnum,
)
from inferia.services.api_gateway.rbac.middleware import get_current_user_from_request
from inferia.services.api_gateway.rbac.authorization import authz_service

router = APIRouter(prefix="/audit", tags=["Audit"])

@router.get("/logs", response_model=List[AuditLogResponse])
async def get_audit_logs(
    user_id: Optional[str] = None,
    action: Optional[str] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    limit: int = Query(100, le=1000),
    skip: int = 0,
    db: AsyncSession = Depends(get_db),
    user_ctx=Depends(get_current_user_from_request),
):
    """
    Retrieve audit logs.
    """
    authz_service.require_permission(user_ctx, PermissionEnum.AUDIT_LOG_LIST)

    filters = AuditLogFilter(
        user_id=user_id,
        action=action,
        start_date=start_date,
        end_date=end_date,
        limit=limit,
        skip=skip
    )
    
    logs = await audit_service.get_logs(db, filters)
    return logs

# Internal endpoint for testing or manual logging if needed
@router.post("/internal/log", response_model=AuditLogResponse)
async def create_internal_log(
    log_data: AuditLogCreate,
    db: AsyncSession = Depends(get_db),
    api_key: str = Security(APIKeyHeader(name="X-Internal-API-Key")),
):
    """
    Manually create an audit log (Internal Only).
    """
    expected_key = os.getenv("INTERNAL_API_KEY", "dev-internal-key")
    if api_key != expected_key:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid Internal API Key"
        )
    return await audit_service.log_event(db, log_data)

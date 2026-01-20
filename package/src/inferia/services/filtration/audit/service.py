from typing import List, Dict, Any, Optional
from datetime import datetime
import uuid
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import desc
from db.models import AuditLog
from audit.api_models import AuditLogFilter, AuditLogCreate

class AuditService:
    def __init__(self):
        pass

    async def log_event(
        self,
        db: AsyncSession,
        event: AuditLogCreate
    ) -> AuditLog:
        """
        Create an immutable audit log entry.
        """
        db_log = AuditLog(
            id=str(uuid.uuid4()),
            timestamp=datetime.utcnow(),
            user_id=event.user_id,
            action=event.action,
            resource_type=event.resource_type,
            resource_id=event.resource_id,
            details=event.details,
            ip_address=event.ip_address,
            status=event.status
        )
        db.add(db_log)
        await db.commit()
        await db.refresh(db_log)
        return db_log

    async def get_logs(
        self,
        db: AsyncSession,
        filters: AuditLogFilter
    ) -> List[AuditLog]:
        """
        Retrieve audit logs based on filters.
        """
        query = select(AuditLog).order_by(desc(AuditLog.timestamp))

        if filters.user_id:
            query = query.where(AuditLog.user_id == filters.user_id)
        
        if filters.action:
            query = query.where(AuditLog.action == filters.action)
        
        if filters.start_date:
            query = query.where(AuditLog.timestamp >= filters.start_date)
            
        if filters.end_date:
            query = query.where(AuditLog.timestamp <= filters.end_date)
            
        query = query.offset(filters.skip).limit(filters.limit)
        
        result = await db.execute(query)
        return result.scalars().all()

audit_service = AuditService()

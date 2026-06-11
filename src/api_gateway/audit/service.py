import asyncio
import logging
from typing import List
from datetime import datetime, timezone
import uuid
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import desc
from sqlalchemy.orm import joinedload
from api_gateway.db.models import AuditLog
from api_gateway.models import AuditLogFilter, AuditLogCreate, AuditLogResponse
from api_gateway.schemas.logging import derive_category

logger = logging.getLogger(__name__)

def utcnow_naive():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class AuditService:
    def __init__(self):
        pass

    async def log_event(
        self,
        db: AsyncSession,
        event: AuditLogCreate
    ) -> None:
        """
        Fire-and-forget audit log write.

        Spawns a background task with its own DB session so the caller's
        request is not blocked by the audit commit round-trip.
        """
        # Capture everything needed — the background task must not
        # reference the caller's db session (it may be closed by then).
        log_data = {
            "id": str(uuid.uuid4()),
            "timestamp": utcnow_naive(),
            "user_id": event.user_id,
            "org_id": event.org_id,
            "action": event.action,
            "category": event.category or derive_category(event.action),
            "resource_type": event.resource_type,
            "resource_id": event.resource_id,
            "details": event.details,
            "ip_address": event.ip_address,
            "status": event.status,
        }

        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._write_audit_log(log_data))
        except RuntimeError:
            # No running loop (e.g., during shutdown) — write synchronously
            await self._write_audit_log(log_data)

    async def log_event_sync(
        self,
        db: AsyncSession,
        event: AuditLogCreate,
    ) -> AuditLog:
        """
        Synchronous audit log write — blocks until committed.
        Only used by the /internal/log endpoint that needs to return the created record.
        """
        category = event.category or derive_category(event.action)

        db_log = AuditLog(
            id=str(uuid.uuid4()),
            timestamp=utcnow_naive(),
            user_id=event.user_id,
            org_id=event.org_id,
            action=event.action,
            category=category,
            resource_type=event.resource_type,
            resource_id=event.resource_id,
            details=event.details,
            ip_address=event.ip_address,
            status=event.status,
        )
        db.add(db_log)
        await db.commit()
        await db.refresh(db_log)
        return db_log

    async def _write_audit_log(self, log_data: dict) -> None:
        """Background writer with its own DB session."""
        try:
            from api_gateway.db.database import AsyncSessionLocal

            async with AsyncSessionLocal() as db:
                db_log = AuditLog(**log_data)
                db.add(db_log)
                await db.commit()
        except Exception as e:
            logger.error("Failed to write audit log: %s", e)

    async def get_logs(
        self,
        db: AsyncSession,
        filters: AuditLogFilter
    ) -> List[AuditLogResponse]:
        """
        Retrieve audit logs with user email joined.
        """
        query = select(AuditLog).options(
            joinedload(AuditLog.user)
        ).order_by(desc(AuditLog.timestamp))

        if filters.user_id:
            query = query.where(AuditLog.user_id == filters.user_id)

        if filters.action:
            query = query.where(AuditLog.action == filters.action)

        if filters.category:
            query = query.where(AuditLog.category == filters.category)

        if filters.org_id:
            query = query.where(AuditLog.org_id == filters.org_id)

        if filters.start_date:
            query = query.where(AuditLog.timestamp >= filters.start_date)

        if filters.end_date:
            query = query.where(AuditLog.timestamp <= filters.end_date)

        query = query.offset(filters.skip).limit(filters.limit)

        result = await db.execute(query)
        logs = result.scalars().unique().all()

        return [
            AuditLogResponse(
                id=log.id,
                timestamp=log.timestamp,
                user_id=log.user_id,
                user_email=log.user.email if log.user else None,
                org_id=log.org_id,
                action=log.action,
                category=log.category or derive_category(log.action),
                resource_type=log.resource_type,
                resource_id=log.resource_id,
                details=log.details,
                ip_address=log.ip_address,
                status=log.status,
            )
            for log in logs
        ]

audit_service = AuditService()

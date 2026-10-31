"""Open-source ``audit_sink`` default (interfaces.md §6): the audit_logs table."""

import logging

from api_gateway.db.models import AuditLog
from common.extensions.protocols import AuditEvent

logger = logging.getLogger(__name__)


class PostgresAuditSink:
    """Writes one ``audit_logs`` row per event using its own DB session, so the
    caller's request-scoped session may already be closed. Errors are logged,
    never raised: audit writes are fire-and-forget on the request path."""

    async def emit(self, event: AuditEvent) -> None:
        try:
            from api_gateway.db.database import AsyncSessionLocal

            async with AsyncSessionLocal() as db:
                db.add(
                    AuditLog(
                        id=event.id,
                        timestamp=event.timestamp,
                        user_id=event.user_id,
                        org_id=event.org_id,
                        action=event.action,
                        category=event.category,
                        resource_type=event.resource_type,
                        resource_id=event.resource_id,
                        details=event.details,
                        ip_address=event.ip_address,
                        status=event.status,
                    )
                )
                await db.commit()
        except Exception as e:
            logger.error("Failed to write audit log: %s", e)

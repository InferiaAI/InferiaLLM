from sqlalchemy import Column, String, DateTime, JSON, ForeignKey, event
from sqlalchemy.orm import relationship
from datetime import datetime, timezone
import uuid
from ..database import Base

def utcnow_naive():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class ImmutableAuditLogError(ValueError):
    """Raised when application code attempts to mutate an existing audit log."""

class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    timestamp = Column(DateTime, default=utcnow_naive, nullable=False)

    user_id = Column(String, ForeignKey("users.id"), nullable=True, index=True)
    action = Column(String, nullable=False, index=True)
    resource_type = Column(String, nullable=True)
    resource_id = Column(String, nullable=True)
    details = Column(JSON, nullable=True)
    ip_address = Column(String, nullable=True)
    status = Column(String, nullable=False, default="success")
    org_id = Column(String, nullable=True, index=True)
    category = Column(String, nullable=True, index=True)

    # Relationships
    user = relationship("User", backref="audit_logs")


def _reject_audit_log_mutation(operation: str) -> None:
    raise ImmutableAuditLogError(
        f"Audit logs are immutable and cannot be {operation}."
    )


@event.listens_for(AuditLog, "before_update")
def _prevent_audit_log_update(mapper, connection, target):
    _reject_audit_log_mutation("updated")


@event.listens_for(AuditLog, "before_delete")
def _prevent_audit_log_delete(mapper, connection, target):
    _reject_audit_log_mutation("deleted")

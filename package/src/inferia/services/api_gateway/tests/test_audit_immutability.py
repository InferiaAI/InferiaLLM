"""Tests for append-only audit log behavior."""

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from inferia.services.api_gateway.db.database import Base
from inferia.services.api_gateway.db.models.audit_log import AuditLog
from inferia.services.api_gateway.db.models.user import User


@pytest.fixture
def audit_session():
    """Provide a minimal in-memory session for audit log model tests."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[User.__table__, AuditLog.__table__])
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = session_factory()

    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine, tables=[AuditLog.__table__, User.__table__])
        engine.dispose()


def make_audit_log() -> AuditLog:
    return AuditLog(
        action="user.login",
        resource_type="user",
        resource_id="user-123",
        details={"source": "test"},
        status="success",
        category="auth",
    )


def test_audit_log_insert_remains_allowed(audit_session):
    audit_log = make_audit_log()

    audit_session.add(audit_log)
    audit_session.commit()

    stored_logs = audit_session.execute(select(AuditLog)).scalars().all()
    assert len(stored_logs) == 1
    assert stored_logs[0].action == "user.login"


def test_audit_log_update_is_rejected(audit_session):
    audit_log = make_audit_log()
    audit_session.add(audit_log)
    audit_session.commit()

    audit_log.action = "user.logout"

    with pytest.raises(ValueError, match="immutable"):
        audit_session.commit()


def test_audit_log_delete_is_rejected(audit_session):
    audit_log = make_audit_log()
    audit_session.add(audit_log)
    audit_session.commit()

    audit_session.delete(audit_log)

    with pytest.raises(ValueError, match="immutable"):
        audit_session.commit()
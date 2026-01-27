from sqlalchemy import Column, String, DateTime, ForeignKey, Enum
from sqlalchemy.orm import relationship
from ..database import Base
import uuid
import datetime

def utcnow_naive():
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)

class Invitation(Base):
    __tablename__ = "invitations"

    id = Column(String, primary_key=True, index=True, default=lambda: str(uuid.uuid4()))
    email = Column(String, index=True, nullable=False)
    role = Column(String, default="member")
    token = Column(String, unique=True, index=True, nullable=False)
    
    org_id = Column(String, ForeignKey("organizations.id"), nullable=False)
    created_by = Column(String, ForeignKey("users.id"), nullable=False)
    
    created_at = Column(DateTime, default=utcnow_naive)
    expires_at = Column(DateTime, nullable=False)
    accepted_at = Column(DateTime, nullable=True)
    
    # Relationships
    organization = relationship("Organization")
    inviter = relationship("User", foreign_keys=[created_by])

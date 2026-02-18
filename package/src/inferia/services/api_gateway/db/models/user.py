from sqlalchemy import Column, String, DateTime, ForeignKey, func, Boolean
from sqlalchemy.orm import relationship
from ..database import Base
import uuid

class User(Base):
    __tablename__ = "users"

    id = Column(String, primary_key=True, index=True, default=lambda: str(uuid.uuid4()))
    email = Column(String, unique=True, index=True, nullable=False)
    password_hash = Column(String, nullable=False)
    
    # TOTP - 2FA
    totp_secret = Column(String, nullable=True)
    totp_enabled = Column(Boolean, default=False)
    
    # Deprecated: usage moved to UserOrganization table
    # role = Column(String, default="member")
    # org_id = Column(String, ForeignKey("organizations.id"), nullable=True)
    
    default_org_id = Column(String, nullable=True) # ID of the organization to use by default on login
    
    # Relationships
    # organization = relationship("Organization", backref="users", lazy="selectin")
    
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

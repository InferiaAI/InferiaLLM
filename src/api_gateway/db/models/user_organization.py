from sqlalchemy import Column, String, DateTime, ForeignKey, func, UniqueConstraint
from sqlalchemy.orm import relationship
from ..database import Base
import uuid

class UserOrganization(Base):
    __tablename__ = "user_organizations"

    id = Column(String, primary_key=True, index=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    org_id = Column(String, ForeignKey("organizations.id"), nullable=False)
    role = Column(String, default="member") # "admin", "member"
    
    # Metadata
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
    
    # Constraint: One role per user per org
    __table_args__ = (
        UniqueConstraint('user_id', 'org_id', name='uq_user_org'),
    )
    
    # Relationships
    # Determined in User/Organization models via backref or defined here
    # user = relationship("User", back_populates="organizations_link")
    # organization = relationship("Organization", back_populates="users_link")

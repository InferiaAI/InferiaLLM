from sqlalchemy import Column, String, DateTime, func, JSON
from ..database import Base
import uuid

class Role(Base):
    __tablename__ = "roles"

    name = Column(String, primary_key=True) # e.g. "admin", "member"
    description = Column(String, nullable=True)
    permissions = Column(JSON, nullable=False, default=[]) # List of permission strings
    
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

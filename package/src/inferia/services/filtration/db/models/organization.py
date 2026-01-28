from sqlalchemy import Column, String, DateTime, func, Boolean
from ..database import Base
import uuid

class Organization(Base):
    __tablename__ = "organizations"

    id = Column(String, primary_key=True, index=True, default=lambda: str(uuid.uuid4()))
    name = Column(String, unique=True, index=True, nullable=False)
    api_key = Column(String, unique=True, index=True, nullable=True) # Or multiple keys relation? keeping simple for now
    log_payloads = Column(Boolean, default=True)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

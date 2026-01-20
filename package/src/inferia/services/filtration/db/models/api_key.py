
from sqlalchemy import Column, String, DateTime, ForeignKey, Boolean
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from datetime import datetime
import uuid
from ..database import Base

class ApiKey(Base):
    __tablename__ = "api_keys"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String, nullable=False)
    key_hash = Column(String, nullable=False, unique=True)
    prefix = Column(String, nullable=False) # Store first few chars for display
    
    # org_id = Column(String, ForeignKey("organizations.id"), nullable=False)
    # organization = relationship("Organization", backref="api_keys")
    org_id = Column(String, nullable=False)

    # Optional: Scope to a specific deployment
    deployment_id = Column(UUID(as_uuid=True), ForeignKey("model_deployments.deployment_id"), nullable=True)
    deployment = relationship("Deployment", backref="api_keys")
    
    is_active = Column(Boolean, default=True)
    last_used_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

from sqlalchemy import Column, String, DateTime, ForeignKey, func, JSON
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from ..database import Base
import uuid

class Policy(Base):
    __tablename__ = "policies"

    id = Column(String, primary_key=True, index=True, default=lambda: str(uuid.uuid4()))
    
    # Type: "guardrails", "rag", "rate_limit"
    policy_type = Column(String, nullable=False)
    
    # Configuration
    # e.g. {"enabled": true, "scanners": ["toxicity"], "threshold": 0.8}
    config_json = Column(JSON, nullable=False)
    
    org_id = Column(String, ForeignKey("organizations.id"), nullable=False)
    
    # Optional: Deployment Specific
    deployment_id = Column(
        UUID(as_uuid=True),
        ForeignKey("model_deployments.deployment_id", ondelete="SET NULL"),
        nullable=True,
    )
    
    # Relationships
    organization = relationship("Organization", backref="policies", lazy="selectin")
    
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())


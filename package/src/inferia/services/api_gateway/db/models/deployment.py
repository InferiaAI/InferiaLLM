from sqlalchemy import Column, String, DateTime, ForeignKey, func, JSON, Integer, ARRAY
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from ..database import Base
import uuid
from inferia.services.api_gateway.db.security import EncryptedJSON

class Deployment(Base):
    __tablename__ = "model_deployments"

    id = Column("deployment_id", UUID(as_uuid=True), primary_key=True, index=True, default=uuid.uuid4)
    model_name = Column(String, nullable=False) # e.g. "gpt-4-turbo" or "meta-llama/..."
    
    # Unified Fields
    engine = Column(String, nullable=True) # e.g. "vllm", "openai"
    configuration = Column(EncryptedJSON, nullable=True) # Automatically encrypted/decrypted
    endpoint = Column(String, nullable=True) # Exposed URL
    
    # Mapped from org_id column (Unified Schema)
    org_id = Column("org_id", String, nullable=True)
    
    # Custom Deployment Name / Resource Name (for sticky routing)
    llmd_resource_name = Column(String, nullable=True)
    
    policies = Column(JSON, nullable=True) # Filtration policies
    inference_model = Column(String, nullable=True) # Backend model slug (e.g. "meta-llama/...")

    # Orchestration Fields (Required for Schema Compatibility)
    model_id = Column(UUID(as_uuid=True), nullable=True)
    pool_id = Column(UUID(as_uuid=True), nullable=True) # Should be NOT NULL in full schema, but nullable safe for partial
    replicas = Column(Integer, default=1)
    gpu_per_replica = Column(Integer, default=0)
    state = Column(String, default="pending")
    owner_id = Column(String, nullable=True)
    
    allocation_ids = Column(ARRAY(UUID(as_uuid=True)), nullable=True)
    node_ids = Column(ARRAY(UUID(as_uuid=True)), nullable=True)
    
    # Legacy fields (optional support or remove?)
    # provider = Column(String, nullable=True) # Access via engine
    
    # Relationships
    # organization = relationship("Organization", backref="deployments", lazy="selectin")
    
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

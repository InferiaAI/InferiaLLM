from sqlalchemy import Column, String, Integer, DateTime, ForeignKey, func, JSON, Boolean, Float
from sqlalchemy.dialects.postgresql import UUID
from ..database import Base
import uuid


class InferenceLog(Base):
    __tablename__ = "inference_logs"

    id = Column(String, primary_key=True, index=True, default=lambda: str(uuid.uuid4()))
    deployment_id = Column(
        UUID(as_uuid=True),
        ForeignKey("model_deployments.deployment_id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    user_id = Column(String, index=True, nullable=False)  # User ID or API key context
    ip_address = Column(String, index=True, nullable=True)  # Client IP supplied by caller/proxy
    
    # Request Info
    request_payload = Column(JSON, nullable=True)  # Full request body (optional to save space)
    model = Column(String, index=True, nullable=False)
    
    # Performance Metrics
    latency_ms = Column(Integer, nullable=True)  # Total request latency in milliseconds
    ttft_ms = Column(Integer, nullable=True)     # Time to first token (for streaming)
    tokens_per_second = Column(Float, nullable=True)  # Output generation speed
    
    # Token Usage
    prompt_tokens = Column(Integer, default=0)
    completion_tokens = Column(Integer, default=0)
    total_tokens = Column(Integer, default=0)
    
    # Response Info
    status_code = Column(Integer, default=200)
    error_message = Column(String, nullable=True)
    is_streaming = Column(Boolean, default=False)
    applied_policies = Column(JSON, nullable=True)  # List of policies applied (e.g. guardrail, pii, template)
    
    created_at = Column(DateTime, default=func.now(), index=True)

from sqlalchemy import Column, String, Integer, DateTime, ForeignKey, func, Date, UniqueConstraint
from ..database import Base
import uuid

class Usage(Base):
    __tablename__ = "usage_stats"

    id = Column(String, primary_key=True, index=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String, index=True, nullable=False) # Can be UserID, OrgID, or ApiKeyID
    
    # Granularity: Daily usage per model
    date = Column(Date, nullable=False, default=func.current_date())
    model = Column(String, nullable=False, index=True)
    
    prompt_tokens = Column(Integer, default=0)
    completion_tokens = Column(Integer, default=0)
    total_tokens = Column(Integer, default=0)
    request_count = Column(Integer, default=0)
    
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint('user_id', 'date', 'model', name='_user_daily_model_usage_uc'),
    )

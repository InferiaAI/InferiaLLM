from pydantic import BaseModel, Field, ConfigDict
from typing import Optional, Dict, Any, Literal
from datetime import datetime
from uuid import uuid4

class StandardHeaders(BaseModel):
    """Standard headers used across all requests."""
    x_request_id: str = Field(default_factory=lambda: str(uuid4()))
    x_user_id: Optional[str] = None
    x_trace_id: Optional[str] = Field(default_factory=lambda: str(uuid4()))
    x_client_version: Optional[str] = None

class ErrorResponse(BaseModel):
    """Standard error response."""
    model_config = ConfigDict(from_attributes=True)
    
    error: str
    message: str
    details: Optional[Dict[str, Any]] = None
    request_id: Optional[str] = None
    timestamp: datetime = Field(default_factory=datetime.utcnow)

class HealthCheckResponse(BaseModel):
    """Health check response."""
    model_config = ConfigDict(from_attributes=True)
    
    status: Literal["healthy", "degraded", "unhealthy"]
    version: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)

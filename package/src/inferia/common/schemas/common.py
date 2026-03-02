from pydantic import BaseModel, Field
from typing import Optional, Dict, Any


class StandardHeaders(BaseModel):
    """Standard request headers for internal tracking."""

    request_id: str = Field(..., alias="X-Request-ID")
    org_id: Optional[str] = Field(None, alias="X-Org-ID")
    user_id: Optional[str] = Field(None, alias="X-User-ID")


class ErrorDetail(BaseModel):
    """Standardized error detail structure."""

    code: str
    message: str
    details: Optional[Dict[str, Any]] = None


class ErrorResponse(BaseModel):
    """Standardized API error response."""

    success: bool = False
    request_id: Optional[str] = None
    error: ErrorDetail


class HealthCheckResponse(BaseModel):
    """Standard health check response."""

    status: str = "healthy"
    version: str
    service: Optional[str] = None
    components: Dict[str, str] = Field(default_factory=dict)

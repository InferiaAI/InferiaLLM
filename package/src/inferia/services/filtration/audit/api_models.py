from pydantic import BaseModel, Field
from typing import Optional, Dict, List, Any
from datetime import datetime

class AuditLogCreate(BaseModel):
    user_id: Optional[str] = None
    action: str
    resource_type: Optional[str] = None
    resource_id: Optional[str] = None
    details: Optional[Dict[str, Any]] = None
    ip_address: Optional[str] = None
    status: str = "success"

class AuditLogResponse(BaseModel):
    id: str
    timestamp: datetime
    user_id: Optional[str]
    action: str
    resource_type: Optional[str]
    resource_id: Optional[str]
    details: Optional[Dict[str, Any]]
    ip_address: Optional[str]
    status: str

    class Config:
        from_attributes = True

class AuditLogFilter(BaseModel):
    user_id: Optional[str] = None
    action: Optional[str] = None
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    limit: int = 100
    skip: int = 0

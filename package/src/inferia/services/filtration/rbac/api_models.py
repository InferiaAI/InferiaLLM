from pydantic import BaseModel, ConfigDict
from typing import List, Optional
from datetime import datetime

class RoleCreate(BaseModel):
    name: str # e.g. "admin"
    description: Optional[str] = None
    permissions: List[str] = []

class RoleUpdate(BaseModel):
    description: Optional[str] = None
    permissions: Optional[List[str]] = None

class RoleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    
    name: str
    description: Optional[str] = None
    permissions: List[str]
    created_at: datetime
    updated_at: datetime

class PermissionResponse(BaseModel):
    permissions: List[str]

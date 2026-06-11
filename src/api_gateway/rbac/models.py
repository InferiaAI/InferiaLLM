"""
RBAC data models for users, roles, and permissions.
"""

from pydantic import BaseModel, EmailStr, Field
from typing import List, Optional, Dict, Any
from datetime import datetime, timezone
from enum import Enum

def utcnow_naive():
    return datetime.now(timezone.utc).replace(tzinfo=None)

class RoleType(str, Enum):
    """Predefined user roles."""
    ADMIN = "admin"
    POWER_USER = "power_user"
    STANDARD_USER = "standard_user"
    GUEST = "guest"


class PermissionType(str, Enum):
    """Predefined permissions."""
    # Model access permissions
    ACCESS_GPT4 = "access:gpt-4"
    ACCESS_GPT35 = "access:gpt-3.5-turbo"
    ACCESS_CLAUDE3 = "access:claude-3"
    ACCESS_LLAMA3 = "access:llama-3"
    ACCESS_MISTRAL = "access:mistral"
    
    # Feature permissions
    STREAMING = "feature:streaming"
    RAG_ACCESS = "feature:rag"
    FINE_TUNING = "feature:fine-tuning"
    
    # Admin permissions
    MANAGE_USERS = "admin:manage-users"
    MANAGE_MODELS = "admin:manage-models"
    VIEW_METRICS = "admin:view-metrics"
    MANAGE_POLICIES = "admin:manage-policies"


class User(BaseModel):
    """User entity."""
    user_id: str
    username: str
    email: EmailStr
    hashed_password: str
    roles: List[RoleType]
    is_active: bool = True
    created_at: datetime = Field(default_factory=utcnow_naive)
    updated_at: datetime = Field(default_factory=utcnow_naive)
    metadata: Dict[str, Any] = {}


class Role(BaseModel):
    """Role entity with associated permissions."""
    role_id: str
    name: RoleType
    description: str
    permissions: List[PermissionType]
    quota_limit: int  # Requests per day
    priority: int = 0  # Higher priority = more privileged


class Permission(BaseModel):
    """Permission entity."""
    permission_id: str
    name: PermissionType
    description: str
    resource_type: str  # e.g., "model", "feature", "admin"
    resource_id: Optional[str] = None  # Specific resource (e.g., "gpt-4")


class Policy(BaseModel):
    """Organization policy for governance."""
    policy_id: str
    name: str
    description: str
    conditions: Dict[str, Any]
    actions: List[str]
    is_active: bool = True


class UserQuota(BaseModel):
    """User quota tracking."""
    user_id: str
    quota_limit: int
    quota_used: int
    reset_at: datetime
    
    @property
    def quota_remaining(self) -> int:
        """Calculate remaining quota."""
        return max(0, self.quota_limit - self.quota_used)
    
    @property
    def is_quota_exceeded(self) -> bool:
        """Check if quota is exceeded."""
        return self.quota_used >= self.quota_limit

from pydantic import BaseModel, ConfigDict
from typing import Optional, List, Literal
from enum import Enum
from datetime import datetime


class PermissionEnum(str, Enum):
    # Core
    ADMIN_ALL = "admin:all"  # Super admin

    # Generic (To be deprecated/removed if possible, but keeping for now if widely used)
    # READ = "read"  # Cleaned up
    # WRITE = "write" # Cleaned up

    # API Keys
    API_KEY_CREATE = "api_key:create"
    API_KEY_LIST = "api_key:list"
    API_KEY_REVOKE = "api_key:revoke"

    # Deployments
    DEPLOYMENT_CREATE = "deployment:create"
    DEPLOYMENT_LIST = "deployment:list"
    DEPLOYMENT_UPDATE = "deployment:update"
    DEPLOYMENT_DELETE = "deployment:delete"

    # Prompt Templates
    PROMPT_CREATE = "prompt_template:create"
    PROMPT_LIST = "prompt_template:list"
    PROMPT_VIEW = "prompt_template:view"
    PROMPT_UPDATE = "prompt_template:update"
    PROMPT_DELETE = "prompt_template:delete"

    # Models
    MODEL_ACCESS = "model:access"

    # Member Management
    MEMBER_INVITE = "member:invite"
    MEMBER_DELETE = "member:delete"
    MEMBER_LIST = "member:list"
    MEMBER_UPDATE = "member:update"

    # Knowledge Base
    KB_CREATE = "knowledge_base:create"
    KB_ADD_DATA = "knowledge_base:add_data"
    KB_DELETE = "knowledge_base:delete"
    KB_LIST = "knowledge_base:list"
    KB_VIEW = "knowledge_base:view"
    KB_UPDATE = "knowledge_base:update"

    # Role Management
    ROLE_CREATE = "role:create"
    ROLE_LIST = "role:list"
    ROLE_View = "role:view"
    ROLE_UPDATE = "role:update"
    ROLE_DELETE = "role:delete"

    # User Management
    USER_LIST = "user:list"
    USER_VIEW = "user:view"
    USER_UPDATE = "user:update"
    USER_DELETE = "user:delete"

    # Audit Logs
    AUDIT_LOG_LIST = "audit_log:list"
    AUDIT_LOG_VIEW = "audit_log:view"

    # Organization
    ORG_VIEW = "organization:view"
    ORG_UPDATE = "organization:update"


class OrganizationBasicInfo(BaseModel):
    id: str
    name: str
    role: str


class AuthToken(BaseModel):
    """JWT token structure."""

    access_token: str
    refresh_token: Optional[str] = None
    token_type: str = "bearer"
    expires_in: int
    organizations: List[OrganizationBasicInfo] = []


class SwitchOrgRequest(BaseModel):
    org_id: str


class LoginRequest(BaseModel):
    """Login request."""

    username: str
    password: str
    totp_code: Optional[str] = None


class TOTPSetupResponse(BaseModel):
    """Response for TOTP setup."""

    secret: str
    qr_code: str  # Base64 encoded QR code or URL


class TOTPVerifyRequest(BaseModel):
    """Request to verify TOTP setup."""

    totp_code: str


class TokenPayload(BaseModel):
    """JWT token payload."""

    sub: str  # subject (user_id)
    exp: int  # expiration time
    iat: int  # issued at
    type: Literal["access", "refresh"]
    roles: List[str] = []
    org_id: Optional[str] = None


class UserContext(BaseModel):
    """User information and permissions extracted from JWT."""

    user_id: str
    username: str
    email: str
    roles: List[str]
    permissions: List[str]
    org_id: Optional[str] = None
    quota_limit: int
    quota_used: int
    is_active: bool = True


class UserInfoResponse(BaseModel):
    """User information response."""

    model_config = ConfigDict(use_enum_values=True, from_attributes=True)

    user_id: str
    username: str
    email: str
    roles: List[str]
    permissions: List[str] = []
    org_id: Optional[str] = None
    created_at: Optional[datetime] = None
    is_active: bool
    totp_enabled: bool = False


class RoleCreate(BaseModel):
    name: str  # e.g. "admin"
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

from pydantic import BaseModel, ConfigDict, Field
from typing import Optional, List, Literal
from enum import Enum
from datetime import datetime


class PermissionEnum(str, Enum):
    # API Keys
    API_KEY_CREATE = "api_key:create"
    API_KEY_LIST = "api_key:list"
    API_KEY_REVOKE = "api_key:revoke"

    # Deployments
    DEPLOYMENT_CREATE = "deployment:create"
    DEPLOYMENT_LIST = "deployment:list"
    DEPLOYMENT_UPDATE = "deployment:update"
    DEPLOYMENT_DELETE = "deployment:delete"

    # Models
    MODEL_ACCESS = "model:access"

    # Models cache
    MODEL_LIST = "model:list"
    MODEL_ADD = "model:add"
    MODEL_DELETE = "model:delete"

    # Member Management
    MEMBER_INVITE = "member:invite"
    MEMBER_DELETE = "member:delete"
    MEMBER_LIST = "member:list"

    # Role Management
    ROLE_CREATE = "role:create"
    ROLE_LIST = "role:list"
    ROLE_UPDATE = "role:update"
    ROLE_DELETE = "role:delete"

    # Audit Logs
    AUDIT_LOG_LIST = "audit_log:list"

    # Organization
    ORG_VIEW = "organization:view"
    ORG_UPDATE = "organization:update"

    # Nodes. A root shell on a worker, so not in the default member role.
    NODE_SHELL = "node:shell"


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


class TOTPDisableRequest(BaseModel):
    """Turning 2FA off needs the password and a current code, not just a session."""

    password: str
    totp_code: str


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8)


class TokenPayload(BaseModel):
    """JWT token payload."""

    sub: str  # subject (user_id)
    exp: int  # expiration time
    iat: int  # issued at
    type: Literal["access", "refresh"]
    roles: List[str] = []
    org_id: Optional[str] = None
    tv: int = 0  # the user's token_version when issued; older tokens have none


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
    # Signed in through this install's own login, not an external IdP.
    local_session: bool = False


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
    # Install-wide settings (roles, provider credentials) are superadmin-only.
    is_superadmin: bool = False


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

from pydantic import BaseModel, ConfigDict, field_validator
from typing import Optional, List, Dict, Any, Literal
from datetime import datetime

# --- Organization ---
class OrganizationCreate(BaseModel):
    name: str
    
class OrganizationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    api_key: Optional[str] = None
    created_at: datetime

# --- User ---
class UserCreate(BaseModel):
    email: str
    password: str
    role: Literal["admin", "member"] = "member"

class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    email: str
    role: str
    org_id: Optional[str] = None
    created_at: datetime

# --- Invitation ---
class InviteRequest(BaseModel):
    email: str
    role: Literal["admin", "member"] = "member"

class InviteResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    email: str
    role: str
    token: str
    invite_link: str
    status: str
    expires_at: datetime
    created_at: datetime

class InvitationListResponse(BaseModel):
    invitations: List[InviteResponse]

class RegisterRequest(BaseModel):
    email: str
    password: str
    organization_name: Optional[str] = None # For new organization
    invite_token: Optional[str] = None # For joining existing organization

class RegisterInviteRequest(BaseModel):
    token: str
    password: str
    name: Optional[str] = None # Optional name if we start supporting it


# --- Deployment ---
class DeploymentCreate(BaseModel):
    name: str # e.g. "My production model" (maps to model_name)
    model_name: str # e.g. "gpt-4"
    provider: str # (maps to engine)
    endpoint_url: Optional[str] = None # (maps to endpoint)
    credentials_json: Dict[str, Any] # (maps to configuration)

class DeploymentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str  # UUID converted to string
    model_name: str
    engine: Optional[str] = None  # Provider type (openai, anthropic, vllm, etc.)
    endpoint: Optional[str] = None  # API endpoint URL
    org_id: Optional[str] = None
    llmd_resource_name: Optional[str] = None  # Custom deployment name
    inference_model: Optional[str] = None  # Backend model identifier
    created_at: Optional[datetime] = None
    
    # Validator to convert UUID to string
    @field_validator('id', mode='before')
    @classmethod
    def convert_uuid_to_str(cls, v):
        if v is not None:
            return str(v)
        return v


# --- API Keys ---
class ApiKeyCreate(BaseModel):
    name: str
    days_valid: int = 30
    deployment_id: Optional[str] = None

class ApiKeyResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    prefix: str
    is_active: bool
    created_at: datetime
    last_used_at: Optional[datetime] = None
    
class ApiKeyCreatedResponse(ApiKeyResponse):
    secret_key: str # The raw key "sk-..."

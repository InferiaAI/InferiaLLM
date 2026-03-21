from pydantic import BaseModel, ConfigDict
from typing import Optional, Dict, Any, Union, List
from datetime import datetime
from uuid import UUID


class InferenceLogCreate(BaseModel):
    deployment_id: str
    user_id: str
    model: str
    ip_address: Optional[str] = None
    request_payload: Optional[Dict[str, Any]] = None
    latency_ms: Optional[int] = None
    ttft_ms: Optional[int] = None
    tokens_per_second: Optional[float] = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    status_code: int = 200
    error_message: Optional[str] = None
    is_streaming: bool = False
    applied_policies: Optional[List[str]] = None


class InferenceLogResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    deployment_id: Union[str, UUID]
    user_id: str
    model: str
    ip_address: Optional[str] = None
    request_payload: Optional[Dict[str, Any]] = None
    latency_ms: Optional[int] = None
    ttft_ms: Optional[int] = None
    tokens_per_second: Optional[float] = None
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    status_code: int
    error_message: Optional[str] = None
    is_streaming: bool
    applied_policies: Optional[List[str]] = None
    created_at: datetime


# Action → category mapping
ACTION_CATEGORY_MAP = {
    "user.login": "auth",
    "user.register_invite": "auth",
    "user.accept_invite": "auth",
    "user.switch_org": "auth",
    "user.create": "user_management",
    "user.2fa_enabled": "security",
    "user.2fa_disabled": "security",
    "deployment.create": "deployment",
    "deployment.delete": "deployment",
    "api_key.create": "api_key",
    "api_key.revoke": "api_key",
    "organization.create": "organization",
    "organization.update": "organization",
    "invitation.create": "organization",
    "invitation.revoke": "organization",
    "credential.create": "credential",
    "credential.update": "credential",
    "credential.delete": "credential",
    "deployment.start": "deployment",
    "deployment.terminate": "deployment",
    "deployment.update": "deployment",
    "pool.create": "deployment",
    "pool.stop": "deployment",
    "pool.delete": "deployment",
    "config.update": "configuration",
    "prompt_template.create": "configuration",
    "prompt_template.delete": "configuration",
    "knowledge_base.add_document": "knowledge_base",
}


def derive_category(action: str) -> str:
    """Derive audit log category from action string."""
    if action in ACTION_CATEGORY_MAP:
        return ACTION_CATEGORY_MAP[action]
    # Fallback: use prefix before the first dot
    prefix = action.split(".")[0] if "." in action else action
    return prefix


class AuditLogCreate(BaseModel):
    user_id: Optional[str] = None
    org_id: Optional[str] = None
    action: str
    resource_type: Optional[str] = None
    resource_id: Optional[str] = None
    details: Optional[Dict[str, Any]] = None
    ip_address: Optional[str] = None
    status: str = "success"
    category: Optional[str] = None  # Auto-derived if not set


class AuditLogResponse(BaseModel):
    id: str
    timestamp: datetime
    user_id: Optional[str]
    user_email: Optional[str] = None
    org_id: Optional[str] = None
    action: str
    category: Optional[str] = None
    resource_type: Optional[str]
    resource_id: Optional[str]
    details: Optional[Dict[str, Any]]
    ip_address: Optional[str]
    status: str

    model_config = ConfigDict(from_attributes=True)


class AuditLogFilter(BaseModel):
    user_id: Optional[str] = None
    action: Optional[str] = None
    category: Optional[str] = None
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    limit: int = 100
    skip: int = 0
    org_id: Optional[str] = None

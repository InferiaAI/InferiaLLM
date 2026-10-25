from pydantic import BaseModel, ConfigDict
from typing import Optional, Dict, Any
from datetime import date, datetime

class ConfigUpdateRequest(BaseModel):
    policy_type: str
    deployment_id: Optional[str] = None
    config_json: Dict[str, Any]

class ConfigResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    policy_type: str
    config_json: Dict[str, Any]
    updated_at: datetime

class UsageStatsResponse(BaseModel):
    key_name: str
    key_prefix: str
    requests: int
    tokens: int

class UsageHistoryRow(BaseModel):
    """One API key's usage of one model on one day."""
    day: date
    key_id: str
    key_name: str
    key_prefix: str
    key_active: bool
    model: str
    requests: int
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int

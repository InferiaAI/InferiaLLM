from pydantic import BaseModel, ConfigDict
from typing import Optional, Dict, Any, Union
from datetime import datetime
from uuid import UUID

class InferenceLogCreate(BaseModel):
    deployment_id: str
    user_id: str
    model: str
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

class InferenceLogResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    
    id: str
    deployment_id: Union[str, UUID]
    user_id: str
    model: str
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
    created_at: datetime

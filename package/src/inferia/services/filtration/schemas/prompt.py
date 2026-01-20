from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from datetime import datetime
from schemas.inference import Message

class PromptProcessRequest(BaseModel):
    messages: List[Message]
    model: str
    user_id: Optional[str] = None
    org_id: Optional[str] = None
    template_id: Optional[str] = None
    template_content: Optional[str] = None
    template_vars: Optional[Dict[str, Any]] = None
    rag_config: Optional[Dict[str, Any]] = None
    template_config: Optional[Dict[str, Any]] = None

class PromptProcessResponse(BaseModel):
    messages: List[Message]
    used_template_id: Optional[str] = None
    rewritten: bool = False
    rag_context_used: bool = False

class PromptTemplateCreate(BaseModel):
    template_id: str
    content: str
    description: Optional[str] = None
    deployment_id: Optional[str] = None

class PromptTemplateResponse(BaseModel):
    template_id: str
    content: str
    description: Optional[str] = None
    updated_at: datetime

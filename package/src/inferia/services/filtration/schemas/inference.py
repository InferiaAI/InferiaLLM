from pydantic import BaseModel, Field, validator
from typing import Optional, List, Dict, Any, Literal
from datetime import datetime, timezone
from uuid import uuid4


def utcnow_naive():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Message(BaseModel):
    """Chat message."""

    role: Literal["system", "user", "assistant"]
    content: str


class InferenceRequest(BaseModel):
    """Standard inference request format."""

    model: str = Field(..., description="Model identifier (e.g., 'gpt-4', 'claude-3')")
    messages: List[Message] = Field(..., description="List of conversation messages")
    temperature: Optional[float] = Field(default=0.7, ge=0.0, le=2.0)
    max_tokens: Optional[int] = Field(default=1000, ge=1, le=4096)
    top_p: Optional[float] = Field(default=1.0, ge=0.0, le=1.0)
    stream: Optional[bool] = Field(default=False)

    # Feature Flags (Client-side configuration override)
    enable_guardrails: bool = Field(
        default=True, description="Enable input/output guardrail scanning"
    )
    enable_rag: bool = Field(default=False, description="Enable RAG context retrieval")
    rag_collection_name: Optional[str] = Field(
        default=None, description="ChromaDB collection for RAG"
    )

    # Prompt Engine Flags
    template_id: Optional[str] = Field(
        default=None, description="ID of the prompt template to use"
    )
    template_vars: Optional[Dict[str, Any]] = Field(
        default=None, description="Variables for template substitution"
    )

    @validator("messages")
    @classmethod
    def validate_messages(cls, v):
        if not v:
            raise ValueError("messages cannot be empty")
        return v


class Usage(BaseModel):
    """Token usage information."""

    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class Choice(BaseModel):
    """Completion choice."""

    index: int
    message: Message
    finish_reason: Literal["stop", "length", "content_filter"]


class InferenceResponse(BaseModel):
    """Standard inference response format."""

    id: str = Field(default_factory=lambda: f"chatcmpl-{uuid4()}")
    object: str = "chat.completion"
    created: int = Field(default_factory=lambda: int(utcnow_naive().timestamp()))
    model: str
    choices: List[Choice]
    usage: Usage

    # Metadata
    request_id: str
    processing_time_ms: float


class ModelInfo(BaseModel):
    """Model metadata."""

    id: str
    model_name: Optional[str] = None
    object: str = "model"
    created: int
    owned_by: str
    permission: List[str] = []
    description: Optional[str] = None


class ModelsListResponse(BaseModel):
    """List of available models."""

    object: str = "list"
    data: List[ModelInfo]

from pydantic import BaseModel, Field
from typing import Optional, List, Literal, Dict, Any
from uuid import uuid4
from datetime import datetime, timezone


def utcnow_naive():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Message(BaseModel):
    """Chat message (OpenAI compatible)."""

    role: Literal["system", "user", "assistant"]
    content: str


class Usage(BaseModel):
    """Token usage information."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class Choice(BaseModel):
    """Completion choice."""

    index: int
    message: Message
    finish_reason: Optional[
        Literal["stop", "length", "content_filter", "tool_calls"]
    ] = None


class ChatCompletionResponse(BaseModel):
    """Standard chat completion response format (OpenAI compatible)."""

    id: str = Field(default_factory=lambda: f"chatcmpl-{uuid4()}")
    object: str = "chat.completion"
    created: int = Field(default_factory=lambda: int(utcnow_naive().timestamp()))
    model: str
    choices: List[Choice]
    usage: Usage

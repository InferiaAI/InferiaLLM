"""
OpenAI-compatible embedding schemas for the inference gateway.
"""

from pydantic import BaseModel, Field
from typing import List, Optional, Union


class EmbeddingRequest(BaseModel):
    """OpenAI-compatible embedding request."""

    input: Union[str, List[str]] = Field(..., description="Input text to embed")
    model: str = Field(..., description="ID of the model to use")
    encoding_format: Optional[str] = Field(
        "float", description="Format: 'float' or 'base64'"
    )
    dimensions: Optional[int] = Field(
        None, description="Number of dimensions (for truncation)"
    )
    user: Optional[str] = Field(None, description="User identifier for tracking")


class Embedding(BaseModel):
    """Single embedding result."""

    object: str = Field("embedding", description="Type of object")
    embedding: List[float] = Field(..., description="The embedding vector")
    index: int = Field(..., description="Index of the embedding in the list")


class EmbeddingUsage(BaseModel):
    """Token usage for embedding request."""

    prompt_tokens: int = Field(..., description="Number of tokens in the prompt")
    total_tokens: int = Field(..., description="Total number of tokens used")


class EmbeddingResponse(BaseModel):
    """OpenAI-compatible embedding response."""

    object: str = Field("list", description="Type of object")
    data: List[Embedding] = Field(..., description="List of embeddings")
    model: str = Field(..., description="Model used for embedding")
    usage: EmbeddingUsage = Field(..., description="Token usage information")

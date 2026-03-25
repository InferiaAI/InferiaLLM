"""
Adapter for embedding models (Infinity, TEI).
"""

from ..external.openai import OpenAIAdapter


class EmbeddingAdapter(OpenAIAdapter):
    """
    Adapter for embedding models (Infinity, TEI).
    These are OpenAI-compatible for embeddings, so we inherit from OpenAIAdapter.
    """

    def get_endpoint_path(self, request_type: str) -> str:
        # Internal embedding engines use /embeddings (no /v1 prefix)
        if request_type == "embedding":
            return "/embeddings"
        return super().get_endpoint_path(request_type)

    def is_external(self) -> bool:
        return False

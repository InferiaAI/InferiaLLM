"""
Adapter for compute-deployed text models (vLLM, Ollama).
"""

from ..external.openai import OpenAIAdapter


class ComputeAdapter(OpenAIAdapter):
    """
    Adapter for compute-deployed models (vLLM, Ollama).
    These are OpenAI-compatible, so we inherit from OpenAIAdapter.
    """

    def is_external(self) -> bool:
        return False

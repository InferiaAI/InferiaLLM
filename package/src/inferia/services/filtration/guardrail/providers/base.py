from abc import ABC, abstractmethod
from typing import Dict, Any
from ..models import GuardrailResult

class GuardrailProvider(ABC):
    """
    Abstract base class for Guardrail Providers.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique identifier for the provider (e.g. 'llm-guard', 'llama-guard')."""
        pass

    @abstractmethod
    async def scan_input(self, text: str, user_id: str, config: Dict[str, Any], metadata: Dict[str, Any] = None) -> GuardrailResult:
        """
        Scan input text/prompt for safety compliance.
        """
        pass

    @abstractmethod
    async def scan_output(self, text: str, output: str, user_id: str, config: Dict[str, Any], metadata: Dict[str, Any] = None) -> GuardrailResult:
        """
        Scan output text for safety compliance.
        """
        pass

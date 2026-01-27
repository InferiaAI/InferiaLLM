import logging
import httpx
import time
from typing import Dict, Any

from .base import GuardrailProvider
from ..models import GuardrailResult, Violation, ViolationType
from ..config import guardrail_settings

logger = logging.getLogger(__name__)

class LakeraProvider(GuardrailProvider):
    """
    Provider for 'lakera-guard' (Lakera AI).
    Uses /v1/prompt_injection endpoint.
    """

    def __init__(self):
        self.settings = guardrail_settings
        self.api_key = None
        self.base_url = "https://api.lakera.ai"
        self._client = httpx.AsyncClient(timeout=10.0)
        self.refresh_config()

    def refresh_config(self):
        """Refresh configuration from settings."""
        if hasattr(self.settings, 'refresh_from_main_settings'):
            self.settings.refresh_from_main_settings()
            
        current_key = self.settings.lakera_api_key
        
        # If key is missing but exists in main settings, attempt to hydrate
        if not current_key:
            try:
                from config import settings
                current_key = settings.providers.guardrails.lakera.api_key
            except ImportError:
                pass
        
        self.api_key = current_key
        if not self.api_key:
            logger.warning("LAKERA_API_KEY not set. Lakera Guard will fail if used.")

    @property
    def name(self) -> str:
        return "lakera-guard"

    async def scan_input(self, text: str, user_id: str, config: Dict[str, Any], metadata: Dict[str, Any] = None) -> GuardrailResult:
        self.refresh_config()
        if not self.api_key:
             logger.error("Lakera API Key missing.")
             return GuardrailResult(
                is_valid=False, 
                sanitized_text=text,
                violations=[Violation(
                    scanner="Lakera",
                    violation_type=ViolationType.EXTERNAL_SERVICE_ERROR,
                    score=1.0,
                    details="Guardrail engine 'lakera-guard' failed: Missing Lakera API Key."
                )]
            )

        start_time = time.time()
        
        # Lakera Endpoint: https://api.lakera.ai/v2/guard
        try:
            response = await self._client.post(
                f"{self.base_url}/v2/guard",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={"messages": [{"role": "user", "content": text}]}
            )
            response.raise_for_status()
            result = response.json()
                
            # Result format: usually returns { "results": [ { "category": "prompt_injection", "flagged": true, "score": 0.99 } ] }
            violations = []
            is_valid = True
            
            # Check for flagged results (List format)
            results_list = result.get("results", [])
            for res in results_list:
                if res.get("flagged"):
                    is_valid = False
                    category = res.get("category", "")
                    violation_type = self._map_category(category)
                    
                    violations.append(Violation(
                        scanner="Lakera",
                        violation_type=violation_type,
                        score=res.get("score", 1.0),
                        details=f"Lakera detected {category.replace('_', ' ')}" if category else "Lakera detected threat"
                    ))
            
            # Handle single object return if not list
            if not results_list and result.get("flagged"):
                 is_valid = False
                 # Try to deduce category if not present in top level
                 category = result.get("category", "") # Some versions might have it
                 violations.append(Violation(
                        scanner="Lakera",
                        violation_type=self._map_category(category),
                        score=result.get("score", 1.0),
                        details=f"Lakera detected {category.replace('_', ' ')}" if category else "Lakera detected violation"
                    ))

            # Filter violations based on config
            allowed_scanners = config.get("input_scanners") if config else None
            if allowed_scanners is not None:
                violations = [v for v in violations if v.violation_type in allowed_scanners]
                
            # Re-evaluate validity based on remaining violations
            is_valid = len(violations) == 0

            return GuardrailResult(
                is_valid=is_valid,
                sanitized_text=text, 
                risk_score=1.0 if not is_valid else 0.0,
                violations=violations,
                scan_time_ms=(time.time() - start_time) * 1000
            )

        except Exception as e:
            logger.error(f"Lakera API failed: {e}")
            # Fail closed? Or Open? 
            # Let's return valid for now to avoid blocking on API error, but log heavily.
            return GuardrailResult(is_valid=True, sanitized_text=text)

    def _map_category(self, category: str) -> ViolationType:
        """Map Lakera category string to ViolationType enum."""
        category = category.lower() if category else ""
        
        mapping = {
            "prompt_injection": ViolationType.PROMPT_INJECTION,
            "jailbreak": ViolationType.PROMPT_INJECTION,
            "hate_speech": ViolationType.HATE,
            "sexual_content": ViolationType.SEXUAL_CONTENT,
            "violence": ViolationType.VIOLENT_CRIMES,
            "toxicity": ViolationType.TOXICITY,
            "pii": ViolationType.PII,
            "unknown": ViolationType.TOXICITY # Default fallback
        }
        
        # Try direct match
        if category in mapping:
            return mapping[category]
            
        # Try substring matching
        if "sex" in category: return ViolationType.SEXUAL_CONTENT
        if "hate" in category: return ViolationType.HATE
        if "viol" in category: return ViolationType.VIOLENT_CRIMES
        if "inject" in category: return ViolationType.PROMPT_INJECTION
        
        return ViolationType.PROMPT_INJECTION # Default for Lakera if unknown

    async def scan_output(self, text: str, output: str, user_id: str, config: Dict[str, Any], metadata: Dict[str, Any] = None) -> GuardrailResult:
        # Lakera is primarily for prompt injection (input).
        # They might have output scanners, but typically we skip or use local for output.
        # For now, pass through.
        return GuardrailResult(is_valid=True, sanitized_text=output)

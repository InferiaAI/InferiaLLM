
import logging
import asyncio
from typing import List, Tuple, Optional
from llm_guard.vault import Vault
from llm_guard.input_scanners import Anonymize
from llm_guard import scan_prompt

from .config import guardrail_settings
from .models import Violation, ViolationType

logger = logging.getLogger(__name__)

class PIIService:
    """
    Dedicated service for PII detection and anonymization.
    Functions independently of the selected Guardrail Engine.
    """
    
    def __init__(self):
        self.settings = guardrail_settings
        self.vault = None
        self._anonymize_cache: dict = {}
        
        # Initialize if PII is enabled.
        # We no longer check enable_llm_guard_startup as it has been deprecated.
        # Users can now control PII loading independently via pii_detection_enabled.
        
        if self.settings.pii_detection_enabled:
            logger.info("Initializing PII Service (Vault + Anonymize)")
            self.vault = Vault()
        else:
            logger.info("PIIService initialization skipped (PII disabled)")

    def _get_anonymize_scanner(self, entity_types: List[str] = None):
        """Get or create cached Anonymize scanner for given entity types."""
        if not self.vault:
            return None
            
        # Use sorted tuple as cache key for consistent hashing
        cache_key = tuple(sorted(entity_types)) if entity_types else "default"
        
        if cache_key not in self._anonymize_cache:
            logger.info(f"Creating new Anonymize scanner for entities: {entity_types or 'ALL'}")
            self._anonymize_cache[cache_key] = Anonymize(vault=self.vault, entity_types=entity_types)
        
        return self._anonymize_cache[cache_key]

    async def anonymize(self, text: str, entities: List[str] = None) -> Tuple[str, List[Violation]]:
        """
        Scan text for PII and return anonymized text + violations.
        """
        if not self.vault:
            # Service not initialized
            return text, []

        scanner = self._get_anonymize_scanner(entities)
        if not scanner:
            return text, []

        try:
            loop = asyncio.get_event_loop()
            # scan_prompt signature: (scanners: list, prompt: str) -> (sanitized_prompt, results_valid, results_score)
            sanitized_text, results_valid, results_score = await loop.run_in_executor(
                None, scan_prompt, [scanner], text
            )
            
            violations = []
            if results_score.get("Anonymize", 0) > 0:
                violations.append(Violation(
                    scanner="Anonymize",
                    violation_type=ViolationType.PII,
                    score=float(results_score.get("Anonymize")),
                    details="PII detected and anonymized"
                ))
            
            return sanitized_text, violations

        except Exception as e:
            logger.error(f"Error in PIIService.anonymize: {e}", exc_info=True)
            return text, []

pii_service = PIIService()

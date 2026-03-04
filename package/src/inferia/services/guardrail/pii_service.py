import logging
import asyncio
from typing import List, Tuple, Optional
from llm_guard.vault import Vault
from llm_guard.input_scanners import Anonymize
from llm_guard import scan_prompt

from inferia.services.guardrail.config import guardrail_settings
from inferia.services.guardrail.models import Violation, ViolationType

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
        self._lock = asyncio.Lock()

    async def _initialize_vault(self):
        """Lazy initialization of Vault with proper locking."""
        if self.vault is None:
            async with self._lock:
                if self.vault is None:
                    logger.info("Initializing PII Service (Vault)")
                    self.vault = Vault()

    async def _get_anonymize_scanner(self, entity_types: List[str] = None):
        """Get or create cached Anonymize scanner for given entity types."""
        if entity_types is not None and len(entity_types) == 0:
            entity_types = None

        logger.info(f"Getting anonymize scanner for {entity_types or 'DEFAULTS'}")
        await self._initialize_vault()

        cache_key = tuple(sorted(entity_types)) if entity_types else "default"

        if cache_key not in self._anonymize_cache:
            async with self._lock:
                if cache_key not in self._anonymize_cache:
                    logger.info(
                        f"Creating new Anonymize scanner for entities: {entity_types or 'ALL'}"
                    )
                    try:
                        self._anonymize_cache[cache_key] = Anonymize(
                            vault=self.vault, entity_types=entity_types
                        )
                        logger.info("Anonymize scanner created successfully")
                    except Exception as e:
                        logger.error(
                            f"Failed to create Anonymize scanner: {e}", exc_info=True
                        )
                        return None

        return self._anonymize_cache[cache_key]

    async def anonymize(
        self, text: str, entities: List[str] = None
    ) -> Tuple[str, List[Violation]]:
        """
        Scan text for PII and return anonymized text + violations.
        """
        scanner = await self._get_anonymize_scanner(entities)
        if not scanner:
            # scanner failed to initialize (e.g. missing vault)
            return text, []

        try:
            loop = asyncio.get_event_loop()
            # scan_prompt signature: (scanners: list, prompt: str) -> (sanitized_prompt, results_valid, results_score)
            sanitized_text, results_valid, results_score = await loop.run_in_executor(
                None, scan_prompt, [scanner], text
            )

            logger.info(f"PII Scan Scores: {results_score}")

            violations = []
            if results_score.get("Anonymize", 0) > 0:
                violations.append(
                    Violation(
                        scanner="Anonymize",
                        violation_type=ViolationType.PII,
                        score=float(results_score.get("Anonymize")),
                        details="PII detected and anonymized",
                    )
                )

            return sanitized_text, violations

        except Exception as e:
            logger.error(f"Error in PIIService.anonymize: {e}", exc_info=True)
            return text, []


pii_service = PIIService()

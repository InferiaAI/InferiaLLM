import logging
from typing import Dict, List, Any

from inferia.services.guardrail.config import guardrail_settings
from inferia.services.guardrail.models import GuardrailResult, Violation, ViolationType
from inferia.services.guardrail.providers.base import GuardrailProvider
from inferia.services.guardrail.providers.llm_guard_provider import LLMGuardProvider
from inferia.services.guardrail.providers.llama_guard_provider import LlamaGuardProvider
from inferia.services.guardrail.providers.lakera_provider import LakeraProvider
from inferia.services.guardrail.pii_service import pii_service

logger = logging.getLogger(__name__)


class GuardrailEngine:
    """
    Guardrail engine for scanning LLM inputs and outputs.
    Follows Provider Pattern to support multiple backends.
    """

    def __init__(self):
        """Initialize guardrail providers."""
        self.settings = guardrail_settings
        self.providers: Dict[str, GuardrailProvider] = {}

        self._load_providers()

        logger.info(
            f"Guardrail engine initialized with providers: {list(self.providers.keys())}"
        )
        logger.info(f"Default provider: {self.settings.default_guardrail_engine}")

    def _load_providers(self):
        """Register available providers."""
        # 1. LLM Guard (Local)
        try:
            llm_guard = LLMGuardProvider()
            self.providers[llm_guard.name] = llm_guard
        except Exception as e:
            logger.error(f"Failed to initialize LLMGuardProvider: {e}", exc_info=True)

        # 2. Llama Guard (Groq)
        try:
            llama_guard = LlamaGuardProvider()
            self.providers[llama_guard.name] = llama_guard
        except Exception as e:
            logger.error(f"Failed to initialize LlamaGuardProvider: {e}", exc_info=True)

        # 3. Lakera Guard (API)
        try:
            lakera_guard = LakeraProvider()
            self.providers[lakera_guard.name] = lakera_guard
        except Exception as e:
            logger.error(f"Failed to initialize LakeraProvider: {e}", exc_info=True)

    async def scan_input(
        self,
        prompt: str,
        user_id: str = None,
        custom_keywords: List[str] = None,
        pii_entities: List[str] = None,
        config: dict = None,
    ) -> GuardrailResult:
        """Scan user input for safety violations."""
        config = config or {}
        metadata = {"custom_keywords": custom_keywords, "pii_entities": pii_entities}
        return await self._execute_scan(prompt, user_id, config, metadata, "input")

    async def _execute_scan(
        self,
        text: str,
        user_id: str,
        config: dict,
        metadata: dict,
        scan_type: str,
    ) -> GuardrailResult:
        """Execute guardrail scan with common logic for both input and output."""
        engine_name = config.get(
            "guardrail_engine", self.settings.default_guardrail_engine
        )

        enabled = self.settings.enable_guardrails
        if "enabled" in config:
            enabled = config["enabled"]

        if not enabled:
            return GuardrailResult(is_valid=True, sanitized_text=text)

        provider = self.providers.get(engine_name)
        if not provider:
            logger.warning(
                f"Requested guardrail engine '{engine_name}' not found. Falling back to default."
            )
            provider = self.providers.get(self.settings.default_guardrail_engine)

        if not provider:
            logger.error("No guardrail providers available.")
            return GuardrailResult(is_valid=True, sanitized_text=text)

        try:
            if scan_type == "input":
                result = await provider.scan_input(text, user_id, config, metadata)
            else:
                result = await provider.scan_output(
                    metadata.get("prompt", ""), text, user_id, config, metadata
                )

            pii_enabled = config.get("pii_enabled", False)
            if "pii_enabled" not in config:
                scanner_key = f"{scan_type}_scanners"
                scanners = config.get(scanner_key, [])
                if "PII" in scanners or "Anonymize" in scanners:
                    pii_enabled = True

            entities = metadata.get("pii_entities") or config.get("pii_entities")

            logger.info(
                f"PII Check ({scan_type}): enabled={pii_enabled}, result_valid={result.is_valid}"
            )

            if pii_enabled and self.settings.pii_detection_enabled and result.is_valid:
                sanitized_text, pii_violations = await pii_service.anonymize(
                    result.sanitized_text or text, entities
                )

                if pii_violations:
                    logger.info(f"PII detected. Violations: {len(pii_violations)}")
                    result.violations.extend(pii_violations)
                    result.sanitized_text = sanitized_text
                    if "anonymized" not in result.actions_taken:
                        result.actions_taken.append("anonymized")
                else:
                    logger.info("No PII detected.")

            proceed_on_violation = config.get("proceed_on_violation", False)

            if not result.is_valid and proceed_on_violation:
                logger.warning(
                    f"Guardrail violation detected but 'proceed_on_violation' is active. User: {user_id}"
                )

                violations_desc = ", ".join(
                    [f"{v.violation_type} ({v.score:.2f})" for v in result.violations]
                )
                warning_suffix = f"\n\n[SYSTEM: Guardrail Violation Detected: {violations_desc}. User Configured to Proceed.]"

                result.is_valid = True
                current_text = result.sanitized_text or text
                result.sanitized_text = current_text + warning_suffix
                result.actions_taken.append("proceed_on_violation_warning")

            return result

        except Exception as e:
            logger.error(
                f"Error executing {scan_type} scan on provider {provider.name}: {e}",
                exc_info=True,
            )
            return GuardrailResult(
                is_valid=False,
                sanitized_text=text,
                violations=[
                    Violation(
                        scanner="Engine",
                        violation_type=ViolationType.EXTERNAL_SERVICE_ERROR,
                        score=1.0,
                        details=f"Guardrail engine error: {str(e)}",
                    )
                ],
            )

    async def scan_output(
        self,
        prompt: str,
        output: str,
        user_id: str = None,
        custom_keywords: List[str] = None,
        pii_entities: List[str] = None,
        config: dict = None,
    ) -> GuardrailResult:
        """Scan model output for safety violations."""
        config = config or {}
        metadata = {
            "prompt": prompt,
            "custom_keywords": custom_keywords,
            "pii_entities": pii_entities,
        }
        return await self._execute_scan(output, user_id, config, metadata, "output")


guardrail_engine = GuardrailEngine()

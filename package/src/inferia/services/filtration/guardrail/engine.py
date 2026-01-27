
import logging
from typing import Dict, List, Any

from .config import guardrail_settings
from .models import GuardrailResult
from .providers.base import GuardrailProvider
from .providers.llm_guard_provider import LLMGuardProvider
from .providers.llama_guard_provider import LlamaGuardProvider
from .providers.lakera_provider import LakeraProvider


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
        
        logger.info(f"Guardrail engine initialized with providers: {list(self.providers.keys())}")
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
        config: dict = None
    ) -> GuardrailResult:
        """
        Scan user input for safety violations.
        Dispatcher for specific provider implementation.
        """
        config = config or {}
        
        # Determine engine
        engine_name = config.get("guardrail_engine", self.settings.default_guardrail_engine)
        
        # Explicit override disable
        enabled = self.settings.enable_guardrails
        if "enabled" in config:
            enabled = config["enabled"]
        
        if not enabled:
            return GuardrailResult(is_valid=True, sanitized_text=prompt)

        provider = self.providers.get(engine_name)
        if not provider:
            # Fallback to default if specified engine not found, or error
            logger.warning(f"Requested guardrail engine '{engine_name}' not found. Falling back to default.")
            provider = self.providers.get(self.settings.default_guardrail_engine)
            
        if not provider:
             # Emergency fallback if even default is missing
             logger.error("No guardrail providers available.")
             return GuardrailResult(is_valid=True, sanitized_text=prompt)

        # Prepare metadata
        metadata = {
            "custom_keywords": custom_keywords,
            "pii_entities": pii_entities
        }

        try:
            result = await provider.scan_input(prompt, user_id, config, metadata)
            
            # Helper logic: Check for 'Proceed on Violation' override
            proceed_on_violation = config.get("proceed_on_violation", False)
            
            if not result.is_valid and proceed_on_violation:
                logger.warning(f"Guardrail violation detected but 'proceed_on_violation' is active. User: {user_id}")
                
                # Construct warning message
                violations_desc = ", ".join([f"{v.violation_type} ({v.score:.2f})" for v in result.violations])
                warning_suffix = f"\n\n[SYSTEM: Guardrail Violation Detected: {violations_desc}. User Configured to Proceed.]"
                
                # Override validity
                result.is_valid = True
                
                # Append warning to sanitized text
                current_text = result.sanitized_text or prompt
                result.sanitized_text = current_text + warning_suffix
                
                # Mark action
                result.actions_taken.append("proceed_on_violation_warning")
                
            return result
            
        except Exception as e:
            logger.error(f"Error executing scan_input on provider {provider.name}: {e}", exc_info=True)
            return GuardrailResult(
                is_valid=False, 
                sanitized_text=prompt,
                violations=[Violation(
                    scanner="Engine",
                    violation_type=ViolationType.EXTERNAL_SERVICE_ERROR,
                    score=1.0,
                    details=f"Guardrail engine error: {str(e)}"
                )]
            )

    async def scan_output(
        self, 
        prompt: str, 
        output: str, 
        user_id: str = None, 
        custom_keywords: List[str] = None,
        config: dict = None
    ) -> GuardrailResult:
        """
        Scan model output for safety violations.
        Dispatcher for specific provider implementation.
        """
        config = config or {}
        
        # Determine engine
        engine_name = config.get("guardrail_engine", self.settings.default_guardrail_engine)
        
        # Explicit override disable
        enabled = self.settings.enable_guardrails
        if "enabled" in config:
            enabled = config["enabled"]
        
        if not enabled:
            return GuardrailResult(is_valid=True, sanitized_text=output)

        provider = self.providers.get(engine_name)
        if not provider:
            logger.warning(f"Requested guardrail engine '{engine_name}' not found. Falling back to default.")
            provider = self.providers.get(self.settings.default_guardrail_engine)
            
        if not provider:
             logger.error("No guardrail providers available.")
             return GuardrailResult(is_valid=True, sanitized_text=output)

        metadata = {
            "custom_keywords": custom_keywords
        }

        try:
            return await provider.scan_output(prompt, output, user_id, config, metadata)
        except Exception as e:
            logger.error(f"Error executing scan_output on provider {provider.name}: {e}", exc_info=True)
            return GuardrailResult(
                is_valid=False, 
                sanitized_text=output,
                violations=[Violation(
                    scanner="Engine",
                    violation_type=ViolationType.EXTERNAL_SERVICE_ERROR,
                    score=1.0,
                    details=f"Guardrail engine error: {str(e)}"
                )]
            )


guardrail_engine = GuardrailEngine()

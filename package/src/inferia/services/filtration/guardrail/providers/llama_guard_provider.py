import logging
import time
import asyncio
from typing import Dict, Any, Tuple, List

try:
    from groq import Groq
except ImportError:
    Groq = None

from .base import GuardrailProvider
from ..models import GuardrailResult
from ..config import guardrail_settings
from ..models import Violation, ViolationType

logger = logging.getLogger(__name__)

class LlamaGuardProvider(GuardrailProvider):
    """
    Provider for 'llama-guard' (Groq API).
    """

    def __init__(self):
        self.settings = guardrail_settings
        self.groq_client = None
        self._last_key = None
        self.initialize_client()

    def initialize_client(self):
        """Initialize or refresh the Groq client if the API key has changed."""
        # Ensure latest main settings are pulled in
        if hasattr(self.settings, 'refresh_from_main_settings'):
            self.settings.refresh_from_main_settings()
            
        current_key = self.settings.groq_api_key
        
        # If key is missing but exists in main settings, attempt to hydrate
        if not current_key:
            try:
                from config import settings
                current_key = settings.providers.guardrails.groq.api_key
                if current_key:
                    logger.info(f"[LlamaGuardProvider] Hydrated key from main settings: {current_key[:6]}...")
            except ImportError:
                logger.error("[LlamaGuardProvider] Could not import config.settings for hydration")
                pass

        if not current_key:
             if self.groq_client:
                 logger.warning("[LlamaGuardProvider] Groq API key removed/missing. Client invalidated.")
                 self.groq_client = None
                 self._last_key = None
             return

        if self.groq_client and current_key == self._last_key:
            return

        if Groq:
            try:
                self.groq_client = Groq(api_key=current_key)
                self._last_key = current_key
                logger.info("[LlamaGuardProvider] Groq client initialized/updated with key starting with " + current_key[:6])
            except Exception as e:
                logger.error(f"[LlamaGuardProvider] Failed to initialize Groq client: {e}")
        else:
            logger.error("[LlamaGuardProvider] Groq library not installed. Cannot initialize client.")

    @property
    def name(self) -> str:
        return "llama-guard"

    async def scan_input(self, text: str, user_id: str, config: Dict[str, Any], metadata: Dict[str, Any] = None) -> GuardrailResult:
        start_time = time.time()
        self.initialize_client()
        
        if not self.groq_client:
            logger.error("Llama Guard requested but Groq client is not initialized.")
            return GuardrailResult(is_valid=True, sanitized_text=text, scan_time_ms=0)

        # Llama Guard prompt template for INPUT is just the prompt itself usually for basic guardrails,
        # but the chat template handles it. We just send it as a user message.
        messages = [{"role": "user", "content": text}]
        
        try:
            loop = asyncio.get_event_loop()
            chat_completion = await loop.run_in_executor(
                None,
                lambda: self.groq_client.chat.completions.create(
                    messages=messages,
                    model=self.settings.llama_guard_model_id,
                )
            )
            
            response = chat_completion.choices[0].message.content
            scan_time_ms = (time.time() - start_time) * 1000
            
            return self._parse_llama_guard_response(response, text, scan_time_ms, "input", config)

        except Exception as e:
            logger.error(f"Error scanning input (Llama Guard): {e}", exc_info=True)
            # Fail open
            return GuardrailResult(is_valid=True, sanitized_text=text, scan_time_ms=(time.time() - start_time) * 1000)

    async def scan_output(self, text: str, output: str, user_id: str, config: Dict[str, Any], metadata: Dict[str, Any] = None) -> GuardrailResult:
        start_time = time.time()
        self.initialize_client()
        
        if not self.groq_client:
            logger.error("Llama Guard requested but Groq client is not initialized.")
            return GuardrailResult(is_valid=True, sanitized_text=output, scan_time_ms=0)

        # For output scanning, we typically send user prompt + agent response
        messages = [
            {"role": "user", "content": text},
            {"role": "assistant", "content": output}
        ]
        
        try:
            loop = asyncio.get_event_loop()
            chat_completion = await loop.run_in_executor(
                None,
                lambda: self.groq_client.chat.completions.create(
                    messages=messages,
                    model=self.settings.llama_guard_model_id,
                )
            )
            
            response = chat_completion.choices[0].message.content
            scan_time_ms = (time.time() - start_time) * 1000
            
            return self._parse_llama_guard_response(response, output, scan_time_ms, "output")

        except Exception as e:
            logger.error(f"Error scanning output (Llama Guard): {e}", exc_info=True)
            return GuardrailResult(is_valid=True, sanitized_text=output, scan_time_ms=(time.time() - start_time) * 1000)

    def _parse_llama_guard_response(self, response: str, text: str, scan_time_ms: float, scan_type: str, config: Dict[str, Any] = None) -> GuardrailResult:
        """Parse raw Llama Guard string response (safe/unsafe + codes)."""
        if not response:
             logger.warning("Empty response from Llama Guard")
             return GuardrailResult(is_valid=True, sanitized_text=text, scan_time_ms=scan_time_ms)
             
        parts = response.split('\n')
        status = parts[0].strip().lower()
        
        if status == "safe":
             return GuardrailResult(is_valid=True, sanitized_text=text, scan_time_ms=scan_time_ms, risk_score=0.0)
        
        if status == "unsafe":
            violations = []
            codes = []
            if len(parts) > 1:
                codes = parts[1].strip().split(',')
            
            allowed_scanners = config.get("input_scanners") if config else None
            
            for code in codes:
                code = code.strip()
                v_type, desc = self._map_llama_guard_code(code)
                
                # Check if this violation type is enabled (if granular config is present)
                # If input_scanners is None or empty, we default to ALL enabled (standard behavior)
                # But typically empty list might mean NONE enabled? 
                # Let's assume: if key exists, strictly follow it. If missing, allow all.
                if allowed_scanners is not None and v_type not in allowed_scanners:
                    continue

                violations.append(Violation(
                    scanner="LlamaGuard",
                    violation_type=v_type,
                    score=1.0, 
                    details=f"Llama Guard violation {code}: {desc} in {scan_type}"
                ))
            
            # If all violations were filtered out, consider it valid
            if not violations:
                return GuardrailResult(
                    is_valid=True,
                    sanitized_text=text, 
                    risk_score=0.0,
                    violations=[],
                    scan_time_ms=scan_time_ms
                )

            return GuardrailResult(
                is_valid=False,
                sanitized_text=text, 
                risk_score=1.0,
                violations=violations,
                scan_time_ms=scan_time_ms
            )
            
        logger.warning(f"Unexpected Llama Guard response: {response}")
        return GuardrailResult(is_valid=True, sanitized_text=text, scan_time_ms=scan_time_ms)

    def _map_llama_guard_code(self, code: str) -> Tuple[ViolationType, str]:
        """Map Llama Guard safety categories to internal types."""
        mapping = {
            "S1": (ViolationType.VIOLENT_CRIMES, "Violent Crimes"),
            "S2": (ViolationType.NON_VIOLENT_CRIMES, "Non-Violent Crimes"),
            "S3": (ViolationType.SEX_RELATED_CRIMES, "Sex-Related Crimes"),
            "S4": (ViolationType.CHILD_EXPLOITATION, "Child Sexual Exploitation"),
            "S5": (ViolationType.DEFAMATION, "Defamation"),
            "S6": (ViolationType.SPECIALIZED_ADVICE, "Specialized Advice"),
            "S7": (ViolationType.PRIVACY, "Privacy"),
            "S8": (ViolationType.INTELLECTUAL_PROPERTY, "Intellectual Property"),
            "S9": (ViolationType.INDISCRIMINATE_WEAPONS, "Indiscriminate Weapons"),
            "S10": (ViolationType.HATE, "Hate"),
            "S11": (ViolationType.SUICIDE_SELF_HARM, "Suicide & Self-Harm"),
            "S12": (ViolationType.SEXUAL_CONTENT, "Sexual Content"),
            "S13": (ViolationType.ELECTIONS, "Elections"),
            "S14": (ViolationType.CODE_INTERPRETER_ABUSE, "Code Interpreter Abuse"),
        }
        default = (ViolationType.BANNED_CONTENT, "Unsafe Content")
        return mapping.get(code, default)

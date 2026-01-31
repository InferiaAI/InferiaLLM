import asyncio
import logging
import time
from typing import Dict, Any, List

from llm_guard.input_scanners import (
    Toxicity,
    PromptInjection,
    Secrets,
    Code
)
from llm_guard.output_scanners import (
    Toxicity as OutputToxicity,
    Sensitive,
    Bias,
    NoRefusal,
    Relevance,
    BanSubstrings
)
from llm_guard import scan_prompt, scan_output

from .base import GuardrailProvider
from ..models import GuardrailResult
from ..config import guardrail_settings
from ..models import Violation, ViolationType

logger = logging.getLogger(__name__)

class LLMGuardProvider(GuardrailProvider):
    """
    Provider for 'llm-guard' (Local Standards).
    """

    def __init__(self):
        self.settings = guardrail_settings
        self.input_scanners = []
        self.output_scanners = []
        
        # Initialize scanners based on granular config
        if True: # Always attempt init, granular flags inside control
            logger.info("[LLMGuardProvider] Initializing scanners based on granular config...")
            self.input_scanners = self._init_input_scanners()
            self.output_scanners = self._init_output_scanners()

    @property
    def name(self) -> str:
        return "llm-guard"

    def _init_input_scanners(self):
        """Initialize input scanners based on configuration."""
        scanners = []
        
        # Toxicity detection
        if self.settings.enable_toxicity:
            scanners.append(Toxicity(threshold=self.settings.toxicity_threshold))
        
        # Prompt Injection (Critical)
        if self.settings.enable_prompt_injection:
            scanners.append(PromptInjection(threshold=self.settings.prompt_injection_threshold))
        
        # Secrets detection
        if self.settings.enable_secrets:
            scanners.append(Secrets())
            
        # Code injection (optional)
        if self.settings.enable_code_scanning:
            scanners.append(Code(languages=["Python", "Java", "JavaScript", "Go", "C++"]))
        
        return scanners

    def _init_output_scanners(self):
        """Initialize output scanners based on configuration."""
        scanners = []
        
        # Toxicity detection
        if self.settings.enable_toxicity:
            scanners.append(OutputToxicity(threshold=self.settings.toxicity_threshold))
        
        # Sensitive data detection (Redaction for output PII)
        if self.settings.enable_sensitive_info:
            scanners.append(Sensitive())
        
        # Bias detection
        if self.settings.enable_bias:
            scanners.append(Bias(threshold=self.settings.bias_threshold))

        # Refusal check (ensure model answers)
        if self.settings.enable_no_refusal:
            scanners.append(NoRefusal())
        
        # Relevance check
        if self.settings.enable_relevance:
             scanners.append(Relevance(threshold=self.settings.relevance_threshold))
        
        # Keyword/Legal blocking via Banned Substrings
        banned_list = self.settings.get_banned_substrings_list()
        if banned_list:
            scanners.append(BanSubstrings(substrings=banned_list, match_type="str")) # simple string match
            
        return scanners

    async def scan_input(self, text: str, user_id: str, config: Dict[str, Any], metadata: Dict[str, Any] = None) -> GuardrailResult:
        custom_keywords = metadata.get("custom_keywords") if metadata else None
        
        if not self.input_scanners and not custom_keywords:
            return GuardrailResult(is_valid=True, sanitized_text=text)

        start_time = time.time()
        
        # Dynamic Scanners (Per-request)
        current_scanners = self.input_scanners.copy()
        
        # Add transient BannedSubstrings if custom keywords provided
        if custom_keywords:
            current_scanners.append(BanSubstrings(substrings=custom_keywords, match_type="str"))

        # Run scan in thread pool as it's CPU intensive and blocking
        sanitized_prompt, results_valid, results_score = await asyncio.to_thread(
            scan_prompt, current_scanners, text
        )
        
        violations = []
        is_valid = True
        
        if not all(results_valid.values()):
            is_valid = False
            for scanner_name, valid in results_valid.items():
                if not valid:
                    score = results_score.get(scanner_name, 1.0)
                    mapped_type = ViolationType.UNKNOWN
                    
                    if scanner_name == "Toxicity": mapped_type = ViolationType.TOXICITY
                    elif scanner_name == "PromptInjection": mapped_type = ViolationType.PROMPT_INJECTION
                    elif scanner_name == "Secrets": mapped_type = ViolationType.SECRETS
                    elif scanner_name == "Code": mapped_type = ViolationType.MALICIOUS_CODE
                    elif scanner_name == "BanSubstrings": mapped_type = ViolationType.KEYWORD_FILTER
                    
                    violations.append(Violation(
                        scanner=scanner_name,
                        violation_type=mapped_type,
                        score=float(score),
                        details=f"Blocked by {scanner_name}"
                    ))

        actions = []
        if sanitized_prompt != text:
            actions.append("sanitized")

        return GuardrailResult(
            is_valid=is_valid,
            sanitized_text=sanitized_prompt,
            risk_score=1.0 if not is_valid else 0.0,
            violations=violations,
            scan_time_ms=(time.time() - start_time) * 1000,
            actions_taken=actions
        )

    async def scan_output(self, text: str, output: str, user_id: str, config: Dict[str, Any], metadata: Dict[str, Any] = None) -> GuardrailResult:
        custom_keywords = metadata.get("custom_keywords") if metadata else None
        
        if not self.output_scanners and not custom_keywords:
            return GuardrailResult(is_valid=True, sanitized_text=output)

        start_time = time.time()
        current_scanners = self.output_scanners.copy()
        
        if custom_keywords:
             current_scanners.append(BanSubstrings(substrings=custom_keywords, match_type="str"))

        # Run scan in thread pool as it's CPU intensive and blocking
        sanitized_output, results_valid, results_score = await asyncio.to_thread(
            scan_output, current_scanners, prompt=text, output=output
        )
        
        violations = []
        is_valid = True
        
        if not all(results_valid.values()):
            is_valid = False
            for scanner_name, valid in results_valid.items():
                if not valid:
                    score = results_score.get(scanner_name, 1.0)
                    mapped_type = ViolationType.UNKNOWN
                    
                    if scanner_name == "Toxicity": mapped_type = ViolationType.TOXICITY
                    elif scanner_name == "Sensitive": mapped_type = ViolationType.PII # Output PII
                    elif scanner_name == "Bias": mapped_type = ViolationType.BIAS
                    elif scanner_name == "NoRefusal": mapped_type = ViolationType.REFUSAL
                    elif scanner_name == "Relevance": mapped_type = ViolationType.RELEVANCE
                    elif scanner_name == "BanSubstrings": mapped_type = ViolationType.KEYWORD_FILTER

                    violations.append(Violation(
                        scanner=scanner_name,
                        violation_type=mapped_type,
                        score=float(score),
                        details=f"Blocked by {scanner_name}"
                    ))
        
        actions = []
        if sanitized_output != output:
             actions.append("sanitized")
             
        # If sensitive info was redacted, it might be valid now but marked as violation?
        # LLM Guard Sensitive scanner usually redacts and returns Valid=True if configured to redact?
        # Actually standard LLM Guard returns Valid=False if sensitive info found depending on config.
        # Assuming defaults: detailed behaviors depend on specific scanner implementation.

        return GuardrailResult(
            is_valid=is_valid,
            sanitized_text=sanitized_output,
            risk_score=1.0 if not is_valid else 0.0,
            violations=violations,
            scan_time_ms=(time.time() - start_time) * 1000,
            actions_taken=actions
        )

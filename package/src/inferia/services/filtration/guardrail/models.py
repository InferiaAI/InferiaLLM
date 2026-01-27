"""
Guardrail data models for safety check results.
"""

from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from enum import Enum


class ViolationType(str, Enum):
    """Types of guardrail violations."""
    TOXICITY = "toxicity"
    PII = "pii"
    PROMPT_INJECTION = "prompt_injection"
    CODE_INJECTION = "code_injection"
    SECRETS = "secrets"
    MALICIOUS_URL = "malicious_url"
    BIAS = "bias"
    SENSITIVE_DATA = "sensitive_data"
    IRRELEVANT = "irrelevant"
    BANNED_CONTENT = "banned_content"
    EXTERNAL_SERVICE_ERROR = "external_service_error"
    MALICIOUS_CODE = "malicious_code"
    KEYWORD_FILTER = "keyword_filter"
    UNKNOWN = "unknown"
    REFUSAL = "refusal"
    RELEVANCE = "relevance"
    
    # Llama Guard Specific
    VIOLENT_CRIMES = "violent_crimes"
    NON_VIOLENT_CRIMES = "non_violent_crimes"
    SEX_RELATED_CRIMES = "sex_related_crimes"
    CHILD_EXPLOITATION = "child_exploitation"
    DEFAMATION = "defamation"
    SPECIALIZED_ADVICE = "specialized_advice"
    PRIVACY = "privacy"
    INTELLECTUAL_PROPERTY = "intellectual_property"
    INDISCRIMINATE_WEAPONS = "indiscriminate_weapons"
    HATE = "hate"
    SUICIDE_SELF_HARM = "suicide_self_harm"
    SEXUAL_CONTENT = "sexual_content"
    ELECTIONS = "elections"
    CODE_INTERPRETER_ABUSE = "code_interpreter_abuse"


class Violation(BaseModel):
    """Individual guardrail violation."""
    scanner: str
    violation_type: ViolationType
    score: float = Field(..., ge=0.0, le=1.0, description="Violation severity score")
    details: Optional[str] = None
    detected_content: Optional[str] = Field(None, description="Content that triggered violation")


class GuardrailResult(BaseModel):
    """Result from guardrail scanning."""
    is_valid: bool = Field(..., description="Whether input/output passed all checks")
    sanitized_text: Optional[str] = Field(None, description="Sanitized version of text")
    risk_score: float = Field(0.0, ge=0.0, le=1.0, description="Overall risk score")
    violations: List[Violation] = Field(default_factory=list)
    scan_time_ms: float = Field(0.0, description="Time taken for scanning")
    actions_taken: List[str] = Field(default_factory=list, description="Actions taken (redacted, blocked, etc.)")
    
    @property
    def has_violations(self) -> bool:
        """Check if there are any violations."""
        return len(self.violations) > 0
    
    def get_violations_by_type(self, violation_type: ViolationType) -> List[Violation]:
        """Get all violations of a specific type."""
        return [v for v in self.violations if v.violation_type == violation_type]


class GuardrailConfig(BaseModel):
    """Configuration for guardrail scanners."""
    
    # Global settings
    enabled: bool = True
    log_violations: bool = True
    proceed_on_violation: bool = False
    
    # Thresholds
    toxicity_threshold: float = 0.7
    prompt_injection_threshold: float = 0.8
    bias_threshold: float = 0.75
    
    # PII settings
    pii_detection_enabled: bool = True
    pii_anonymize: bool = True  # Anonymize vs just detect
    pii_entities: List[str] = Field(
        default_factory=lambda: [
            "EMAIL_ADDRESS",
            "PHONE_NUMBER",
            "CREDIT_CARD",
            "US_SSN",
            "IP_ADDRESS",
            "PERSON",
            "LOCATION"
        ]
    )
    
    # Content filtering
    banned_substrings: List[str] = Field(default_factory=list)
    detect_code_injection: bool = True
    detect_secrets: bool = True
    detect_malicious_urls: bool = True
    
    # Output-specific
    check_relevance: bool = True
    relevance_threshold: float = 0.5
    check_bias: bool = True
    
    # Performance
    max_scan_time_seconds: float = 5.0
